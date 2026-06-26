#!/usr/bin/env python3
"""Lightweight maintenance for the SIQ_analysis profile.

Archives old session JSON files and vacuums SQLite state stores. The script is
conservative by default: sessions are moved, not deleted.
"""

from __future__ import annotations

import argparse
import gzip
import json
import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


PROFILE_DIR = Path("/home/maoyd/.hermes/profiles/siq_analysis")


def archive_sessions(profile_dir: Path, older_than_days: int, dry_run: bool) -> dict[str, Any]:
    sessions_dir = profile_dir / "sessions"
    archive_dir = sessions_dir / "archive"
    cutoff = datetime.now().timestamp() - timedelta(days=older_than_days).total_seconds()
    candidates = [
        path for path in sessions_dir.glob("*.json")
        if path.is_file() and path.stat().st_mtime < cutoff
    ]
    archived: list[str] = []
    if not dry_run:
        archive_dir.mkdir(parents=True, exist_ok=True)
    for path in sorted(candidates):
        target = archive_dir / f"{path.name}.gz"
        archived.append(str(target))
        if dry_run:
            continue
        with path.open("rb") as src, gzip.open(target, "wb") as dst:
            shutil.copyfileobj(src, dst)
        path.unlink()
    return {
        "older_than_days": older_than_days,
        "candidate_count": len(candidates),
        "archived": archived,
    }


def vacuum_sqlite(path: Path, dry_run: bool) -> dict[str, Any]:
    before = path.stat().st_size if path.exists() else 0
    if path.exists() and not dry_run:
        conn = sqlite3.connect(str(path))
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            conn.execute("VACUUM")
        finally:
            conn.close()
    after = path.stat().st_size if path.exists() else 0
    return {
        "path": str(path),
        "exists": path.exists(),
        "before_bytes": before,
        "after_bytes": after,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile-dir", type=Path, default=PROFILE_DIR)
    parser.add_argument("--archive-sessions-older-than-days", type=int, default=30)
    parser.add_argument("--vacuum", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--write-json", type=Path)
    args = parser.parse_args()

    result = {
        "ok": True,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "profile_dir": str(args.profile_dir),
        "dry_run": args.dry_run,
        "sessions": archive_sessions(
            args.profile_dir,
            args.archive_sessions_older_than_days,
            args.dry_run,
        ),
        "sqlite": [],
    }
    if args.vacuum:
        for name in ["state.db", "response_store.db"]:
            result["sqlite"].append(vacuum_sqlite(args.profile_dir / name, args.dry_run))

    if args.write_json:
        args.write_json.parent.mkdir(parents=True, exist_ok=True)
        args.write_json.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
