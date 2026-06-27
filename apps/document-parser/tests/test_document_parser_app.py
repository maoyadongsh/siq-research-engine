from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import time
import zipfile
from pathlib import Path


def load_app(tmp_path):
    base = Path(__file__).resolve().parents[1]
    os.environ["SIQ_DOCUMENT_PARSE_DATA_DIR"] = str(tmp_path / "data")
    sys.path.insert(0, str(base))
    spec = importlib.util.spec_from_file_location("document_parser_app_test", base / "app.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module.app.test_client()


def wait_for_terminal(client, task_id, timeout=5.0):
    deadline = time.time() + timeout
    last_payload = {}
    while time.time() < deadline:
        response = client.get(f"/api/status/{task_id}")
        assert response.status_code == 200
        last_payload = response.json
        if last_payload["status"] in {"completed", "completed_with_warnings", "failed", "cancelled"}:
            return last_payload
        time.sleep(0.05)
    raise AssertionError(f"task did not finish: {last_payload}")


def test_markdown_upload_generates_normalized_artifacts(tmp_path):
    client = load_app(tmp_path)
    payload = b"# Contract\n\nparty_a: Alice\n\nparty_b: Bob\n"

    response = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(payload), "sample.md")},
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    task = response.json["tasks"][0]
    assert task["status"] == "queued"
    task_id = task["task_id"]
    task = wait_for_terminal(client, task_id)
    assert task["status"] == "completed"

    result = client.get(f"/api/result/{task_id}")
    assert result.status_code == 200
    assert "DOC_BLOCK" in result.json["markdown"]
    assert result.json["manifest"]["document_kind"] == "text"
    assert result.json["artifacts"]["blocks.json"]["exists"] is True

    blocks = client.get(f"/api/artifact/{task_id}/blocks.json")
    assert blocks.status_code == 200
    assert blocks.json["schema_version"] == "document_blocks_v1"
    assert blocks.json["blocks"]


def test_batch_download_includes_completed_task_packages(tmp_path):
    client = load_app(tmp_path)
    first = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(b"# A\n\nhello\n"), "a.md")},
        content_type="multipart/form-data",
    ).json["tasks"][0]["task_id"]
    second = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(b"# B\n\nworld\n"), "b.md")},
        content_type="multipart/form-data",
    ).json["tasks"][0]["task_id"]
    wait_for_terminal(client, first)
    wait_for_terminal(client, second)

    response = client.post("/api/download/batch", json={"task_ids": [first, second, "missing-task"]})

    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.data)) as archive:
        names = set(archive.namelist())
        assert "batch_manifest.json" in names
        manifest = json.loads(archive.read("batch_manifest.json").decode("utf-8"))
        assert manifest["task_count"] == 2
        assert "missing-task" in manifest["missing"]
        assert any(name.startswith(f"{first}/") and name.endswith(".zip") for name in names)
        assert any(name.startswith(f"{second}/") and name.endswith(".zip") for name in names)


def test_import_mineru_output_dir_normalizes_artifacts_and_raw_archive(tmp_path):
    client = load_app(tmp_path)
    source_dir = tmp_path / "data" / "legacy-mineru" / "case-a"
    images_dir = source_dir / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "chart.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (source_dir / "result.md").write_text("# Imported Case\n\n![Chart](images/chart.png)\n", encoding="utf-8")
    content_items = [
        {"type": "text", "text": "Imported title", "text_level": 1, "page_idx": 0, "bbox": [1, 2, 100, 30]},
        {
            "type": "table",
            "table_caption": ["Key metrics"],
            "table_body": "<table><tr><th>Metric</th><th>Value</th></tr><tr><td>Revenue</td><td>42</td></tr></table>",
            "page_idx": 0,
            "bbox": [4, 40, 200, 90],
        },
        {
            "type": "image",
            "img_path": "images/chart.png",
            "image_caption": ["Revenue chart"],
            "page_idx": 1,
            "bbox": [10, 20, 300, 240],
        },
    ]
    (source_dir / "content_list.json").write_text(json.dumps(json.dumps(content_items), ensure_ascii=False), encoding="utf-8")
    (source_dir / "middle.json").write_text(json.dumps(json.dumps({"pdf_info": [{}, {}]}), ensure_ascii=False), encoding="utf-8")

    response = client.post(
        "/api/import/mineru",
        json={"source_dir": str(source_dir), "task_id": "import-case-a", "language": "zh"},
    )

    assert response.status_code == 200
    task = response.json["task"]
    assert task["task_id"] == "import-case-a"
    assert task["status"] == "completed"
    assert task["parser_provider"] == "mineru_import"

    result = client.get("/api/result/import-case-a")
    assert result.status_code == 200
    assert result.json["manifest"]["raw_artifacts"] == "raw/mineru"
    assert "images/original/chart.png" in result.json["markdown"]

    blocks = client.get("/api/artifact/import-case-a/blocks.json")
    assert blocks.status_code == 200
    assert len(blocks.json["blocks"]) == 3
    assert blocks.json["blocks"][0]["source_ref"]["path"] == "raw/mineru/content_list.json"

    tables = client.get("/api/artifact/import-case-a/tables.json")
    assert tables.status_code == 200
    table = tables.json["physical_tables"][0]
    assert table["caption"] == "Key metrics"
    assert table["quality"]["row_count"] == 2
    assert table["cells"][2]["text"] == "Revenue"

    figures = client.get("/api/figures/import-case-a")
    assert figures.status_code == 200
    figure = figures.json["figures"][0]
    assert figure["image_path"] == "images/original/chart.png"
    assert figure["bbox"] == [10.0, 20.0, 300.0, 240.0]

    image = client.get("/api/artifact/import-case-a/images/original/chart.png")
    assert image.status_code == 200
    raw_content = client.get("/api/artifact/import-case-a/raw/mineru/content_list.json")
    assert raw_content.status_code == 200

    source_image = client.get("/api/source/import-case-a/image/img-000001")
    assert source_image.status_code == 200
    assert source_image.json["crop_url"] == "/api/artifact/import-case-a/images/original/chart.png"

    candidates = client.get("/api/import/mineru/candidates?limit=5")
    assert candidates.status_code == 200
    assert any(item["source_dir"] == str(source_dir) for item in candidates.json["candidates"])

    package = client.get("/api/download/import-case-a")
    assert package.status_code == 200
    with zipfile.ZipFile(io.BytesIO(package.data)) as archive:
        names = set(archive.namelist())
        assert "raw/mineru/content_list.json" in names
        assert "images/original/chart.png" in names


def test_rule_based_schema_extraction_with_evidence_and_cache(tmp_path):
    client = load_app(tmp_path)
    response = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(b"party_a: Alice\nparty_b: Bob\namount: 100 USD\n"), "contract.md")},
        content_type="multipart/form-data",
    )
    task_id = response.json["tasks"][0]["task_id"]
    wait_for_terminal(client, task_id)

    extract = client.post(
        f"/api/extract/{task_id}",
        json={
            "schema": {
                "type": "object",
                "properties": {
                    "party_a": {"type": "string"},
                    "party_b": {"type": "string"},
                },
            }
        },
    )

    assert extract.status_code == 200
    assert extract.json["result"] == {"party_a": "Alice", "party_b": "Bob"}
    assert extract.json["evidence_map"]["party_a"][0]["block_id"]
    assert extract.json["validation_report"]["evidence_coverage_ratio"] == 1.0

    cached = client.post(
        f"/api/extract/{task_id}",
        json={
            "schema": {
                "type": "object",
                "properties": {
                    "party_a": {"type": "string"},
                    "party_b": {"type": "string"},
                },
            }
        },
    )
    assert cached.status_code == 200
    assert cached.json["extract_id"] == extract.json["extract_id"]
    assert cached.json["cached"] is True

    result_artifact = client.get(f"/api/artifact/{task_id}/extraction/result.json")
    assert result_artifact.status_code == 200
    assert result_artifact.json["result"]["party_a"] == "Alice"


def test_template_extraction_lists_templates_and_keeps_missing_fields_null(tmp_path):
    client = load_app(tmp_path)
    templates = client.get("/api/extraction/templates")
    assert templates.status_code == 200
    template_ids = {item["template_id"] for item in templates.json["templates"]}
    assert "contract_terms_v1" in template_ids

    response = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO("甲方: 上海甲公司\n乙方: 北京乙公司\n合同金额: 42万元\n".encode("utf-8")), "contract.md")},
        content_type="multipart/form-data",
    )
    task_id = response.json["tasks"][0]["task_id"]
    wait_for_terminal(client, task_id)

    extract = client.post(f"/api/extract/{task_id}", json={"template_id": "contract_terms_v1"})
    assert extract.status_code == 200
    assert extract.json["template_id"] == "contract_terms_v1"
    assert extract.json["result"]["party_a"] == "上海甲公司"
    assert extract.json["result"]["party_b"] == "北京乙公司"
    assert extract.json["result"]["amount"] == "42万元"
    assert extract.json["result"]["term"] is None
    assert extract.json["validation_report"]["missing_fields"]


def test_image_upload_generates_figure_and_source_map_contracts(tmp_path):
    client = load_app(tmp_path)
    response = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(b"\x89PNG\r\n\x1a\n"), "diagram.png")},
        content_type="multipart/form-data",
    )
    task_id = response.json["tasks"][0]["task_id"]
    task = wait_for_terminal(client, task_id)

    assert task["status"] == "completed_with_warnings"

    figures = client.get(f"/api/figures/{task_id}")
    assert figures.status_code == 200
    figure = figures.json["figures"][0]
    assert figure["image_id"] == "img-000001"
    assert figure["markdown_anchor"] == "md-img-000001"
    assert figure["image_path"] == "images/original/diagram.png"

    source_map = client.get(f"/api/artifact/{task_id}/source_map.json")
    assert source_map.status_code == 200
    image_sources = [item for item in source_map.json["sources"] if item["source_type"] == "image_block"]
    assert image_sources
    assert image_sources[0]["image_id"] == "img-000001"

    source_image = client.get(f"/api/source/{task_id}/image/img-000001")
    assert source_image.status_code == 200
    assert source_image.json["page_number"] == 1
    assert source_image.json["bbox"] == []
    assert source_image.json["image_url"] == f"/api/artifact/{task_id}/images/original/diagram.png"
    assert source_image.json["crop_url"] == f"/api/artifact/{task_id}/images/original/diagram.png"

    quality = client.get(f"/api/artifact/{task_id}/quality_report.json")
    assert quality.status_code == 200
    assert quality.json["image_quality"]["image_count"] == 1


def test_spreadsheet_upload_generates_table_contracts(tmp_path):
    openpyxl = __import__("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Revenue"
    sheet.append(["Year", "Amount"])
    sheet.append(["2025", "100"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)

    client = load_app(tmp_path)
    response = client.post(
        "/api/tasks",
        data={"files": (buffer, "table.xlsx")},
        content_type="multipart/form-data",
    )
    task_id = response.json["tasks"][0]["task_id"]
    task = wait_for_terminal(client, task_id)

    assert task["status"] == "completed"

    tables = client.get(f"/api/artifact/{task_id}/tables.json")
    assert tables.status_code == 200
    assert tables.json["schema_version"] == "document_tables_v1"
    table = tables.json["physical_tables"][0]
    assert table["title"] == "Revenue"
    assert table["quality"]["row_count"] == 2

    logical_tables = client.get(f"/api/artifact/{task_id}/logical_tables.json")
    assert logical_tables.status_code == 200
    assert logical_tables.json["logical_tables"][0]["fragment_table_ids"] == [table["table_id"]]

    review = client.post(
        f"/api/table-relations/{task_id}/rel-001/review",
        json={"review_status": "rejected", "note": "manual check"},
    )
    assert review.status_code == 200
    assert review.json["success"] is True
    assert review.json["corrections"]["relations"]["rel-001"]["review_status"] == "rejected"

    relations = client.get(f"/api/table-relations/{task_id}")
    assert relations.status_code == 200
    assert relations.json["relations"][0]["review_status"] == "rejected"
    assert relations.json["relations"][0]["review_note"] == "manual check"

    invalid_review = client.post(
        f"/api/table-relations/{task_id}/rel-001/review",
        json={"review_status": "maybe"},
    )
    assert invalid_review.status_code == 400
