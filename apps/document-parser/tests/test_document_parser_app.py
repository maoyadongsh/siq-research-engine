from __future__ import annotations

import importlib.util
import io
import os
import sys
import time
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


def test_rule_based_schema_extraction(tmp_path):
    client = load_app(tmp_path)
    response = client.post(
        "/api/tasks",
        data={"files": (io.BytesIO(b"party_a: Alice\nparty_b: Bob\n"), "contract.md")},
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
