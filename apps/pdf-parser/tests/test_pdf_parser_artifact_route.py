import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

import app


def _task(task_id="artifact-route"):
    return {
        "task_id": task_id,
        "mineru_task_id": None,
        "filename": "report.pdf",
        "file_size": 1,
        "pdf_page_count": 1,
        "status": "completed",
        "stage": "completed",
        "created_at": "2026-05-01T00:00:00Z",
        "uploaded_at": "2026-05-01T00:00:00Z",
        "submitted_at": None,
        "started_at": None,
        "completed_at": "2026-05-01T00:01:00Z",
        "cancelled": False,
        "error": None,
        "markdown_path": None,
        "upload_path": None,
        "last_progress_log_time": None,
        "last_status_payload": None,
        "last_polled_at": None,
        "consecutive_status_failures": 0,
        "submit_config": {},
        "logs": [],
    }


def _artifact_client(tmp_path, monkeypatch):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    monkeypatch.setattr(app, "RESULTS_FOLDER", str(results_dir))
    monkeypatch.setattr(app, "DB_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setattr(app, "initialize_app", lambda start_worker=True: None)
    app._init_db()
    task = _task()
    app._save_task(task, allow_insert=True)
    return app.app.test_client(), results_dir, task


def test_open_artifact_route_serves_allowlisted_file_with_nosniff(tmp_path, monkeypatch):
    client, results_dir, task = _artifact_client(tmp_path, monkeypatch)
    result_dir = results_dir / task["task_id"]
    result_dir.mkdir()
    (result_dir / "quality_report.json").write_text('{"ok": true}\n', encoding="utf-8")

    response = client.get(f"/api/artifact/{task['task_id']}/quality_report.json")

    assert response.status_code == 200
    assert response.mimetype == "application/json"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.get_data(as_text=True) == '{"ok": true}\n'


def test_open_artifact_route_lists_and_downloads_images(tmp_path, monkeypatch):
    client, results_dir, task = _artifact_client(tmp_path, monkeypatch)
    images_dir = results_dir / task["task_id"] / "images"
    images_dir.mkdir(parents=True)
    (images_dir / "b.png").write_bytes(b"png")
    (images_dir / "a.jpg").write_bytes(b"jpg")

    listing = client.get(f"/api/artifact/{task['task_id']}/images")

    assert listing.status_code == 200
    assert listing.get_json() == {
        "task_id": task["task_id"],
        "artifact": "images",
        "count": 2,
        "images": [
            {"name": "a.jpg", "url": f"/api/artifact/{task['task_id']}/images/a.jpg"},
            {"name": "b.png", "url": f"/api/artifact/{task['task_id']}/images/b.png"},
        ],
    }

    image = client.get(f"/api/artifact/{task['task_id']}/images/b.png")
    assert image.status_code == 200
    assert image.mimetype == "image/png"
    assert image.headers["X-Content-Type-Options"] == "nosniff"
    assert image.get_data() == b"png"

    (images_dir / "chart.webp").write_bytes(b"webp")
    webp = client.get(f"/api/artifact/{task['task_id']}/images/chart.webp")
    assert webp.status_code == 200
    assert webp.mimetype == "image/jpeg"

    archive = client.get(f"/api/artifact/{task['task_id']}/images/download")
    assert archive.status_code == 200
    assert archive.mimetype == "application/zip"
    assert archive.headers["X-Content-Type-Options"] == "nosniff"
    assert f"filename={task['task_id']}_images.zip" in archive.headers["Content-Disposition"]


def test_open_artifact_route_preserves_error_responses(tmp_path, monkeypatch):
    client, results_dir, task = _artifact_client(tmp_path, monkeypatch)
    os.makedirs(results_dir / task["task_id"], exist_ok=True)

    missing_task = client.get("/api/artifact/missing-task/quality_report.json")
    assert missing_task.status_code == 404
    assert missing_task.get_json() == {"error": "Task not found"}

    forbidden = client.get(f"/api/artifact/{task['task_id']}/secret.txt")
    assert forbidden.status_code == 403
    assert forbidden.get_json() == {"error": "Artifact is not openable"}

    missing_artifact = client.get(f"/api/artifact/{task['task_id']}/quality_report.json")
    assert missing_artifact.status_code == 404
    assert missing_artifact.get_json() == {"error": "Artifact not found"}

    missing_images = client.get(f"/api/artifact/{task['task_id']}/images")
    assert missing_images.status_code == 404
    assert missing_images.get_json() == {"error": "Images artifact not found"}

    empty_images_dir = results_dir / task["task_id"] / "images"
    empty_images_dir.mkdir()
    empty_download = client.get(f"/api/artifact/{task['task_id']}/images/download")
    assert empty_download.status_code == 404
    assert empty_download.get_json() == {"error": "No downloadable images found"}


def test_from_download_reference_enqueues_allowed_pdf(tmp_path, monkeypatch):
    downloads_root = tmp_path / "downloads"
    source_path = downloads_root / "HK" / "00005" / "report.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"%PDF-1.4\nreferenced")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    monkeypatch.setenv("SIQ_PDF_REFERENCE_ROOTS", str(downloads_root))
    monkeypatch.setattr(app, "UPLOAD_FOLDER", str(uploads_dir))
    monkeypatch.setattr(app, "DB_PATH", str(tmp_path / "tasks-reference.db"))
    monkeypatch.setattr(app, "initialize_app", lambda start_worker=True: None)
    monkeypatch.setattr(app, "_cleanup_old_data", lambda: None)
    monkeypatch.setattr(app, "_wake_queue_worker", lambda: None)
    monkeypatch.setattr(app, "_looks_like_pdf", lambda _path: True)
    monkeypatch.setattr(app, "_get_pdf_page_count", lambda _path: 7)
    app._init_db()

    response = app.app.test_client().post(
        "/api/tasks/from-download",
        json={
            "source_path": str(source_path),
            "filename": "report.pdf",
            "market": "HK",
            "backend": "hybrid-http-client",
            "parse_method": "auto",
            "formula_enable": "true",
            "table_enable": "true",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    task_id = payload["task_id"]
    task = app._get_task(task_id)
    assert task["filename"] == "report.pdf"
    assert task["status"] == "queued"
    assert task["pdf_page_count"] == 7
    assert task["submit_config"]["market"] == "HK"
    assert os.path.exists(task["upload_path"])
    assert os.path.commonpath([task["upload_path"], str(uploads_dir)]) == str(uploads_dir)
    assert task["logs"][0]["message"].startswith("服务端引用入队")


def test_from_download_reference_rejects_market_path_mismatch(tmp_path, monkeypatch):
    downloads_root = tmp_path / "downloads"
    source_path = downloads_root / "HK" / "00005" / "report.pdf"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"%PDF-1.4\nreferenced")

    monkeypatch.setenv("SIQ_PDF_REFERENCE_ROOTS", str(downloads_root))
    monkeypatch.setattr(app, "initialize_app", lambda start_worker=True: None)
    monkeypatch.setattr(app, "_cleanup_old_data", lambda: None)

    response = app.app.test_client().post(
        "/api/tasks/from-download",
        json={
            "source_path": str(source_path),
            "download_relative_path": "HK/00005/report.pdf",
            "filename": "report.pdf",
            "market": "CN",
            "backend": "hybrid-http-client",
            "parse_method": "auto",
        },
    )

    assert response.status_code == 400
    assert response.get_json() == {"error": "引用文件属于 HK，不能按 CN 解析"}


def test_from_download_reference_rejects_path_outside_allowed_root(tmp_path, monkeypatch):
    downloads_root = tmp_path / "downloads"
    downloads_root.mkdir()
    outside_path = tmp_path / "outside.pdf"
    outside_path.write_bytes(b"%PDF-1.4\noutside")
    monkeypatch.setenv("SIQ_PDF_REFERENCE_ROOTS", str(downloads_root))
    monkeypatch.setattr(app, "initialize_app", lambda start_worker=True: None)
    monkeypatch.setattr(app, "_cleanup_old_data", lambda: None)

    response = app.app.test_client().post(
        "/api/tasks/from-download",
        json={
            "source_path": str(outside_path),
            "filename": "outside.pdf",
            "market": "HK",
            "backend": "hybrid-http-client",
            "parse_method": "auto",
        },
    )

    assert response.status_code == 400
    assert response.get_json()["error"] == "引用文件路径不在允许目录内"
