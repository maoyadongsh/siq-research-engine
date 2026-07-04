from __future__ import annotations

import importlib.util
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
