from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "enqueue_download_manifest_pdfs.py"
SPEC = importlib.util.spec_from_file_location("enqueue_download_manifest_pdfs", SCRIPT_PATH)
enqueue_download_manifest_pdfs = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(enqueue_download_manifest_pdfs)


def test_main_forwards_manifest_market_to_pdf_upload(monkeypatch, tmp_path):
    pdf_path = tmp_path / "Example_JP_9999_2025-03-31_年报.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    manifest_path = tmp_path / "manifest.json"
    output_path = tmp_path / "enqueue.json"
    manifest_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "seed": {"market": "JP", "ticker": "9999"},
                        "downloaded_file": {"saved_path": str(pdf_path)},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    captured = []

    def fake_upload(pdf_api_base, token, path, *, market=None):
        captured.append({"api": pdf_api_base, "token": token, "path": path, "market": market})
        return {"status_code": 200, "payload": {"task_id": "task-jp"}}

    monkeypatch.setattr(enqueue_download_manifest_pdfs, "_resolve_pdf_token", lambda: "secret-token")
    monkeypatch.setattr(enqueue_download_manifest_pdfs, "_existing_task_filenames", lambda path: set())
    monkeypatch.setattr(enqueue_download_manifest_pdfs, "_upload_pdf", fake_upload)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "enqueue_download_manifest_pdfs.py",
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
            "--task-db",
            str(tmp_path / "tasks.db"),
        ],
    )

    exit_code = enqueue_download_manifest_pdfs.main()

    assert exit_code == 0
    assert captured[0]["market"] == "JP"
    result = json.loads(output_path.read_text(encoding="utf-8"))
    assert result["items"][0]["status"] == "queued"
