from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "download_kr_2025_annuals_to_parse_queue.py"
SPEC = importlib.util.spec_from_file_location("download_kr_2025_annuals_to_parse_queue", SCRIPT_PATH)
assert SPEC is not None
kr_download = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = kr_download
assert SPEC.loader is not None
SPEC.loader.exec_module(kr_download)


def test_normalize_kr_code_pads_to_six_digits():
    assert kr_download._normalize_kr_code("5930") == "005930"
    assert kr_download._normalize_kr_code("KR:270") == "000270"
    assert kr_download._normalize_kr_code("") == ""


def test_requested_codes_accepts_repeated_and_combined_values():
    args = SimpleNamespace(code=["5930", "000660"], codes="270, 12330 373220")

    assert kr_download._requested_codes(args) == ["005930", "000660", "000270", "012330", "373220"]


def test_candidate_pool_defaults_to_30_catalog_entries():
    pool = kr_download._candidate_pool([])

    assert len(pool) == 30
    assert pool[0]["ticker"] == "005930"
    assert pool[-1]["ticker"] == "097950"
    assert all(seed["market"] == "KR" for seed in pool)


def test_candidate_pool_keeps_manual_unknown_codes():
    pool = kr_download._candidate_pool(["005930", "123456"])

    assert pool[0]["ticker"] == "005930"
    assert pool[0]["name"] == "Samsung Electronics Co., Ltd."
    assert pool[1] == {"market": "KR", "ticker": "123456", "industry": "manual", "name": "123456"}


def test_candidate_pool_uses_manual_company_name_for_single_unknown_code():
    pool = kr_download._candidate_pool(["028260"], company_name="Samsung C&T Corporation")

    assert pool == [
        {
            "market": "KR",
            "ticker": "028260",
            "industry": "manual",
            "name": "Samsung C&T Corporation",
        }
    ]


def test_company_for_seed_accepts_manual_kr_ticker_outside_catalog():
    company = kr_download._company_for_seed(
        {
            "market": "KR",
            "ticker": "028260",
            "industry": "construction / trading",
            "name": "Samsung C&T Corporation",
        }
    )

    assert company.market.value == "KR"
    assert company.ticker == "028260"
    assert company.company_name == "Samsung C&T Corporation"
    assert company.match_reason == "manual_kr_ticker"
    assert company.metadata["stock_code"] == "028260"


def test_partition_candidate_pool_marks_requested_skips_for_manifest():
    pool = [
        {"market": "KR", "ticker": "005930", "name": "Samsung Electronics Co., Ltd."},
        {"market": "KR", "ticker": "000660", "name": "SK hynix Inc."},
        {"market": "KR", "ticker": "373220", "name": "LG Energy Solution, Ltd."},
    ]

    active, skipped = kr_download._partition_candidate_pool(pool, {"000660", "373220"})

    assert [seed["ticker"] for seed in active] == ["005930"]
    assert skipped == [
        {
            "seed": {"market": "KR", "ticker": "000660", "name": "SK hynix Inc."},
            "status": "skipped",
            "reason": "Skipped by --skip-code",
        },
        {
            "seed": {"market": "KR", "ticker": "373220", "name": "LG Energy Solution, Ltd."},
            "status": "skipped",
            "reason": "Skipped by --skip-code",
        },
    ]


def test_existing_downloaded_pdf_for_ticker_finds_2025_annual_pdf(tmp_path: Path):
    pdf_path = (
        tmp_path
        / "KR"
        / "Samsung-Electronics-Co.,-Ltd"
        / "2025"
        / "年报"
        / "Samsung-Electronics-Co.,-Ltd_KR_005930_2025-12-31_年报_2026-03-10_dart_public_a4d8816f.pdf"
    )
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(b"%PDF-1.7\n")

    assert kr_download._existing_downloaded_pdf_for_ticker(tmp_path, "005930", 2025) == pdf_path
    assert kr_download._existing_downloaded_pdf_for_ticker(tmp_path, "000660", 2025) is None


def test_existing_tasks_by_filename_returns_task_ids(tmp_path: Path):
    db_path = tmp_path / "tasks.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE tasks (task_id TEXT PRIMARY KEY, filename TEXT NOT NULL)")
        conn.execute("INSERT INTO tasks(task_id, filename) VALUES (?, ?)", ("task-123", "one.pdf"))
        conn.execute("INSERT INTO tasks(task_id, filename) VALUES (?, ?)", ("task-456", "two.pdf"))
        conn.commit()
    finally:
        conn.close()

    assert kr_download._existing_tasks_by_filename(db_path) == {
        "one.pdf": "task-123",
        "two.pdf": "task-456",
    }


def test_upload_pdf_submits_kr_market(tmp_path: Path, monkeypatch):
    pdf_path = tmp_path / "Samsung-Electronics-Co.,-Ltd_KR_005930_2025-12-31_年报_2026-03-10_dart_public_a4d8816f.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    captured = {}

    class DummyResponse:
        status_code = 202
        text = ""

        def json(self):
            return {"task_id": "task-kr-1"}

    class DummyClient:
        def __init__(self, *, timeout, headers):
            captured["headers"] = headers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def post(self, url, *, data, files):
            captured["url"] = url
            captured["data"] = dict(data)
            captured["filename"] = files[0][1][0]
            return DummyResponse()

    monkeypatch.setattr(kr_download.httpx, "Client", DummyClient)

    result = kr_download._upload_pdf("http://127.0.0.1:15000/", "secret-token", pdf_path)

    assert result == {"status_code": 202, "payload": {"task_id": "task-kr-1"}}
    assert captured["headers"] == {"X-PDF2MD-Token": "secret-token"}
    assert captured["url"] == "http://127.0.0.1:15000/api/upload"
    assert captured["filename"] == pdf_path.name
    assert captured["data"]["market"] == "KR"


def test_enqueue_or_mark_uses_existing_task_id_for_duplicate_queue_entry(tmp_path: Path):
    pdf_path = tmp_path / "duplicate.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    args = SimpleNamespace(download_only=False, pdf_api_base="http://127.0.0.1:15000")
    item = {}

    queued = kr_download._enqueue_or_mark(
        item=item,
        pdf_path=pdf_path,
        args=args,
        pdf_token="token",
        existing_tasks_by_filename={"duplicate.pdf": "task-dup-1"},
    )

    assert queued is True
    assert item["status"] == "already_in_queue"
    assert item["reason"] == "filename already exists in pdf-parser tasks"
    assert item["task_id"] == "task-dup-1"


def test_enqueue_or_mark_marks_2xx_without_task_id_as_upload_failed(tmp_path: Path, monkeypatch):
    pdf_path = tmp_path / "fresh.pdf"
    pdf_path.write_bytes(b"%PDF-1.7\n")
    args = SimpleNamespace(download_only=False, pdf_api_base="http://127.0.0.1:15000")
    item = {}

    monkeypatch.setattr(
        kr_download,
        "_upload_pdf",
        lambda pdf_api_base, token, pdf_path: {"status_code": 202, "payload": {"tasks": [{"filename": "fresh.pdf"}]}},
    )

    queued = kr_download._enqueue_or_mark(
        item=item,
        pdf_path=pdf_path,
        args=args,
        pdf_token="token",
        existing_tasks_by_filename={},
    )

    assert queued is False
    assert item["status"] == "upload_failed"
    assert item["reason"] == "Upload succeeded but parser returned no task_id"


def test_main_counts_only_active_candidates_and_records_skips(tmp_path: Path, monkeypatch):
    download_root = tmp_path / "downloads"
    pdf_path = (
        download_root
        / "KR"
        / "Samsung-Electronics-Co.,-Ltd"
        / "2025"
        / "年报"
        / "Samsung-Electronics-Co.,-Ltd_KR_005930_2025-12-31_年报_2026-03-10_dart_public_a4d8816f.pdf"
    )
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.7\n")
    manifest_path = tmp_path / "manifest.json"

    class DummyAnnual:
        def model_dump(self, mode="json"):
            return {"report_year": 2025, "status": "dummy"}

    class DummyDownloader:
        def download(self, annual):
            return SimpleNamespace(
                model_dump=lambda mode="json": {"saved_path": str(pdf_path), "file_name": pdf_path.name},
                saved_path=str(pdf_path),
            )

    monkeypatch.setenv("MARKET_REPORT_DOWNLOAD_DIR", str(download_root))
    monkeypatch.setattr(
        kr_download,
        "_candidate_pool",
        lambda include_codes, company_name=None: [
            {"market": "KR", "ticker": "005930", "name": "Samsung Electronics Co., Ltd."},
            {"market": "KR", "ticker": "000660", "name": "SK hynix Inc."},
            {"market": "KR", "ticker": "373220", "name": "LG Energy Solution, Ltd."},
        ],
    )
    monkeypatch.setattr(kr_download, "_resolve_pdf_token", lambda pdf_api_base: "token")
    monkeypatch.setattr(kr_download, "_existing_tasks_by_filename", lambda db_path: {})
    monkeypatch.setattr(
        kr_download,
        "_existing_downloaded_pdf_for_ticker",
        lambda download_root, ticker, report_year: pdf_path if ticker == "005930" else None,
    )
    monkeypatch.setattr(
        kr_download,
        "_company_for_seed",
        lambda seed: SimpleNamespace(model_dump=lambda mode="json": {"ticker": seed["ticker"]}),
    )
    monkeypatch.setattr(kr_download, "_selected_annual", lambda public, company, year: DummyAnnual())
    monkeypatch.setattr(kr_download, "DartPublicClient", lambda: SimpleNamespace())
    monkeypatch.setattr(kr_download, "ReportDownloader", lambda: DummyDownloader())
    monkeypatch.setattr(kr_download.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "download_kr_2025_annuals_to_parse_queue.py",
            "--codes",
            "005930,000660,373220",
            "--skip-code",
            "000660",
            "--manifest",
            str(manifest_path),
            "--download-only",
        ],
    )

    exit_code = kr_download.main()

    assert exit_code == 0
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["target_count"] == 2
    assert manifest["downloaded_or_existing_count"] == 2
    assert [item["seed"]["ticker"] for item in manifest["items"]] == ["005930", "373220"]
    assert manifest["skipped"] == [
        {
            "seed": {"market": "KR", "ticker": "000660", "name": "SK hynix Inc."},
            "status": "skipped",
            "reason": "Skipped by --skip-code",
        }
    ]


def test_main_errors_when_explicit_target_exceeds_active_candidates_after_skips(tmp_path: Path, monkeypatch):
    manifest_path = tmp_path / "manifest.json"

    class DummyAnnual:
        def model_dump(self, mode="json"):
            return {"report_year": 2025, "status": "dummy"}

    class DummyDownloader:
        def download(self, annual):
            return SimpleNamespace(
                model_dump=lambda mode="json": {"saved_path": str(tmp_path / "unused.pdf"), "file_name": "unused.pdf"},
                saved_path=str(tmp_path / "unused.pdf"),
            )

    monkeypatch.setattr(
        kr_download,
        "_candidate_pool",
        lambda include_codes, company_name=None: [
            {"market": "KR", "ticker": "005930", "name": "Samsung Electronics Co., Ltd."},
            {"market": "KR", "ticker": "000660", "name": "SK hynix Inc."},
            {"market": "KR", "ticker": "373220", "name": "LG Energy Solution, Ltd."},
        ],
    )
    monkeypatch.setattr(kr_download, "DartPublicClient", lambda: SimpleNamespace())
    monkeypatch.setattr(kr_download, "ReportDownloader", lambda: DummyDownloader())
    monkeypatch.setattr(kr_download, "_resolve_pdf_token", lambda pdf_api_base: "token")
    monkeypatch.setattr(kr_download, "_existing_tasks_by_filename", lambda db_path: {})
    monkeypatch.setattr(kr_download, "_existing_downloaded_pdf_for_ticker", lambda download_root, ticker, report_year: None)
    monkeypatch.setattr(
        kr_download,
        "_company_for_seed",
        lambda seed: SimpleNamespace(model_dump=lambda mode="json": {"ticker": seed["ticker"]}),
    )
    monkeypatch.setattr(kr_download, "_selected_annual", lambda public, company, year: DummyAnnual())
    monkeypatch.setattr(kr_download.time, "sleep", lambda seconds: None)

    errors = []

    def fake_error(self, message):
        errors.append(message)
        raise SystemExit(2)

    monkeypatch.setattr(argparse.ArgumentParser, "error", fake_error)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "download_kr_2025_annuals_to_parse_queue.py",
            "--codes",
            "005930,000660,373220",
            "--skip-code",
            "000660",
            "--target-count",
            "3",
            "--manifest",
            str(manifest_path),
            "--download-only",
        ],
    )

    raised = False
    try:
        kr_download.main()
    except SystemExit as exc:
        raised = True
        assert exc.code == 2

    assert raised
    assert errors == ["--target-count cannot exceed the number of active candidates after applying --skip-code"]
