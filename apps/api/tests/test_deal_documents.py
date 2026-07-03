import sys
from io import BytesIO
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from services import deal_documents
from services import deal_store


def _create_package(tmp_path: Path) -> Path:
    deal_store.create_deal_package(
        deal_id="DEAL-DOCS-001",
        company_name="Deal Docs",
        wiki_root=tmp_path,
    )
    return tmp_path / "deals" / "DEAL-DOCS-001"


def test_create_deal_document_sanitizes_filename_and_syncs_manifest(tmp_path):
    package_dir = _create_package(tmp_path)

    document = deal_documents.create_deal_document(
        deal_id="DEAL-DOCS-001",
        filename=r"..\secret\Plan.PDF",
        content_type="application/pdf",
        stream=BytesIO(b"deal data"),
        document_type="business_plan",
        source_note="founder upload",
        wiki_root=tmp_path,
    )

    assert document["original_filename"] == "Plan.PDF"
    assert document["filename"].endswith(".pdf")
    assert document["storage_path"].startswith("data_room/raw/DOC-")
    assert not document["storage_path"].startswith("/")
    assert (package_dir / document["storage_path"]).read_bytes() == b"deal data"

    manifest = deal_store.read_json(package_dir / "manifest.json", {})
    assert manifest["documents"][0]["document_id"] == document["document_id"]
    assert manifest["documents"][0]["storage_path"] == document["storage_path"]


def test_create_deal_document_rejects_oversize_and_removes_partial_file(tmp_path):
    package_dir = _create_package(tmp_path)

    with pytest.raises(ValueError, match="exceeds max upload size"):
        deal_documents.create_deal_document(
            deal_id="DEAL-DOCS-001",
            filename="large.pdf",
            content_type="application/pdf",
            stream=BytesIO(b"too-large"),
            wiki_root=tmp_path,
            max_bytes=3,
        )

    assert list((package_dir / "data_room" / "raw").glob("DOC-*")) == []
    assert deal_documents.list_deal_documents("DEAL-DOCS-001", wiki_root=tmp_path) == []


def test_deal_document_id_validation_rejects_path_values(tmp_path):
    _create_package(tmp_path)

    with pytest.raises(ValueError):
        deal_documents.get_deal_document("DEAL-DOCS-001", "../DOC-ABC", wiki_root=tmp_path)
