import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[1] / "ingest_document_chunks.py"
    spec = importlib.util.spec_from_file_location("ingest_document_chunks", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def build_package(root: Path) -> Path:
    package = root / "wiki" / "documents" / "default" / "demo-task"
    write_json(package / "manifest.json", {
        "schema_version": "generic_document_package_v1",
        "document_id": "doc-task-a",
        "task_id": "task-a",
        "collection": "default",
        "filename": "demo.pdf",
        "document_kind": "pdf",
    })
    write_json(package / "sections" / "blocks.json", {
        "blocks": [
            {"block_id": "b1", "type": "paragraph", "text": "Main paragraph", "page_number": 2, "source_ref": {"evidence_id": "e-block"}},
            {"block_id": "b2", "type": "image", "text": "skip image block", "page_number": 2},
        ]
    })
    write_json(package / "tables" / "tables.json", {
        "physical_tables": [{"table_id": "t1", "title": "Revenue", "page_number": 3, "markdown": "| Year | Amount |\n| --- | --- |\n| 2025 | 100 |"}]
    })
    write_json(package / "figures" / "figures.json", {
        "figures": [{"image_id": "img1", "block_id": "b2", "caption": "System diagram", "ocr_text": "Node A", "page_number": 4}]
    })
    write_json(package / "extraction" / "result.json", {"result": {"party_a": "Alice"}})
    write_json(package / "qa" / "source_map.json", {
        "sources": [
            {"evidence_id": "e-block", "block_id": "b1", "open_source_url": "/api/documents/source/task-a/page/2?block=b1"},
            {"evidence_id": "e-table", "table_id": "t1", "open_source_url": "/api/documents/source/task-a/table/t1"},
            {"evidence_id": "e-image", "image_id": "img1", "open_source_url": "/api/documents/source/task-a/image/img1"},
        ]
    })
    return package


def test_generic_document_chunks_include_source_metadata(tmp_path):
    module = _load_module()
    package = build_package(tmp_path)

    chunks = module.iter_chunks(package)

    types = {item["metadata"]["chunk_type"] for item in chunks}
    assert {"section", "table_summary", "image_caption", "extraction_field"} <= types
    section = next(item for item in chunks if item["metadata"]["chunk_type"] == "section")
    assert section["metadata"]["evidence_id"] == "e-block"
    assert section["metadata"]["open_source_url"].endswith("block=b1")
    table = next(item for item in chunks if item["metadata"]["chunk_type"] == "table_summary")
    assert table["metadata"]["table_id"] == "t1"
    image = next(item for item in chunks if item["metadata"]["chunk_type"] == "image_caption")
    assert image["metadata"]["image_id"] == "img1"
    assert image["metadata"]["source_domain"] == "generic_document"
    assert image["metadata"]["milvus_collection"] == "siq_documents"


def test_write_jsonl(tmp_path):
    module = _load_module()
    package = build_package(tmp_path)
    output = tmp_path / "chunks.jsonl"

    chunks = module.iter_chunks(package)
    module.write_jsonl(chunks, output)

    lines = output.read_text(encoding="utf-8").splitlines()
    assert len(lines) == len(chunks)
    assert json.loads(lines[0])["chunk_uid"]
