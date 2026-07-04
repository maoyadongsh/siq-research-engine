#!/usr/bin/env python3
"""Enqueue PDFs listed in a download manifest into the pdf-parser queue."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

import httpx


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_MARKETS = {"CN", "HK", "US", "EU", "JP", "KR", "DOC"}


def _existing_task_filenames(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute("SELECT filename FROM tasks").fetchall()
        return {str(row[0]) for row in rows if row and row[0]}
    finally:
        conn.close()


def _resolve_pdf_token() -> str:
    token = (os.environ.get("PDF2MD_ACCESS_TOKEN") or os.environ.get("SIQ_PDF2MD_ACCESS_TOKEN") or "").strip()
    if token:
        return token
    for child in Path("/proc").iterdir():
        if not child.name.isdigit():
            continue
        try:
            cmdline = (child / "cmdline").read_bytes().replace(b"\0", b" ").decode("utf-8", "ignore")
            if "siq-research-engine/apps/pdf-parser/app.py" not in cmdline:
                continue
            env = (child / "environ").read_bytes().split(b"\0")
        except OSError:
            continue
        for item in env:
            if item.startswith(b"PDF2MD_ACCESS_TOKEN="):
                return item.split(b"=", 1)[1].decode("utf-8", "ignore").strip()
    raise RuntimeError("PDF2MD access token not found")


def _normalize_market(value: object) -> str | None:
    market = str(value or "").strip().upper()
    return market if market in SUPPORTED_MARKETS else None


def _market_for_manifest_item(item: dict) -> str | None:
    seed = item.get("seed") if isinstance(item.get("seed"), dict) else {}
    downloaded = item.get("downloaded_file") if isinstance(item.get("downloaded_file"), dict) else {}
    for value in (item.get("market"), seed.get("market"), downloaded.get("market")):
        market = _normalize_market(value)
        if market:
            return market
    return None


def _upload_pdf(pdf_api_base: str, token: str, pdf_path: Path, *, market: str | None = None) -> dict:
    headers = {"X-PDF2MD-Token": token} if token else {}
    data = {
        "backend": "hybrid-http-client",
        "parse_method": "auto",
        "formula_enable": "true",
        "table_enable": "true",
    }
    normalized_market = _normalize_market(market)
    if normalized_market:
        data["market"] = normalized_market
    with httpx.Client(timeout=None, headers=headers) as client:
        with pdf_path.open("rb") as infile:
            response = client.post(
                f"{pdf_api_base.rstrip('/')}/api/upload",
                data=data,
                files=[("files", (pdf_path.name, infile, "application/pdf"))],
            )
    try:
        payload = response.json()
    except ValueError:
        payload = {"raw": response.text[:500]}
    return {"status_code": response.status_code, "payload": payload}


def _iter_manifest_paths(manifest_path: Path):
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for item in data.get("items", []):
        downloaded = item.get("downloaded_file") or {}
        saved = downloaded.get("saved_path")
        if not saved:
            continue
        path = Path(saved)
        if not path.exists() and "/downloads/HK/" in saved:
            path = Path(saved.replace("/downloads/HK/", "/data/market-report-finder/downloads/HK/"))
        yield item, path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", default=str(PROJECT_ROOT / "data" / "market-report-finder" / "hk_2025_annual_download_manifest.json"))
    parser.add_argument("--pdf-api-base", default=os.environ.get("SIQ_PDF2MD_API_BASE", "http://127.0.0.1:15000"))
    parser.add_argument("--task-db", default=str(PROJECT_ROOT / "data" / "pdf-parser" / "db" / "tasks.db"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output", default=str(PROJECT_ROOT / "data" / "market-report-finder" / "hk_2025_annual_enqueue_manifest.json"))
    args = parser.parse_args()

    token = _resolve_pdf_token()
    existing = _existing_task_filenames(Path(args.task_db))
    result = {"manifest": args.manifest, "items": [], "skipped": []}
    count = 0
    for item, path in _iter_manifest_paths(Path(args.manifest)):
        if args.limit and count >= args.limit:
            break
        row = {"seed": item.get("seed"), "path": str(path)}
        if not path.is_file():
            row["status"] = "missing"
            result["skipped"].append(row)
            continue
        if path.name in existing:
            row["status"] = "already_in_queue"
            result["items"].append(row)
            count += 1
            continue
        market = _market_for_manifest_item(item)
        if market:
            row["market"] = market
        upload = _upload_pdf(args.pdf_api_base, token, path, market=market)
        row["upload"] = upload
        if 200 <= upload["status_code"] < 300:
            row["status"] = "queued"
            row["task_id"] = upload["payload"].get("task_id")
            existing.add(path.name)
            result["items"].append(row)
            count += 1
        elif upload["status_code"] == 409:
            row["status"] = "already_in_queue"
            row["detail"] = upload["payload"]
            existing.add(path.name)
            result["items"].append(row)
            count += 1
        else:
            row["status"] = "upload_failed"
            row["detail"] = upload["payload"]
            result["skipped"].append(row)
        Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[{count}] {path.name} {row['status']}", flush=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"queued_or_existing": count, "output": args.output}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
