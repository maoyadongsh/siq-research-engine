#!/usr/bin/env python3
"""Backfill missing market values in the PDF parser task database."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
import sqlite3

try:
    import flask  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover - runtime fallback for lean environments
    import types

    class _DummyFlask:
        def __init__(self, *args, **kwargs):
            self.config = {}

        def route(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

        def before_request(self, func=None):
            def decorator(func):
                return func

            return decorator if func is None else func

        def errorhandler(self, *args, **kwargs):
            def decorator(func):
                return func

            return decorator

    sys.modules.setdefault(
        "flask",
        types.SimpleNamespace(
            Flask=_DummyFlask,
            jsonify=lambda *args, **kwargs: None,
            make_response=lambda value: types.SimpleNamespace(
                value=value,
                set_cookie=lambda *args, **kwargs: None,
            ),
            render_template=lambda *args, **kwargs: "",
            request=types.SimpleNamespace(
                args={},
                files={},
                form={},
                headers={},
                cookies={},
                get_json=lambda silent=True: {},
            ),
            send_file=lambda *args, **kwargs: None,
        ),
    )


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

import app  # noqa: E402


def _task_rows(db_path: Path):
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute("SELECT task_id, filename, submit_config_json FROM tasks ORDER BY created_at ASC").fetchall()]


def _load_submit_config(raw: str | None) -> dict:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _task_market(task: dict) -> str | None:
    task = dict(task)
    task["submit_config"] = _load_submit_config(task.pop("submit_config_json", None))
    return app._task_market_from_record(task)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=app.DB_PATH, help="Task database path.")
    parser.add_argument("--dry-run", action="store_true", help="Only report changes without writing.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum rows to update.")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    rows = _task_rows(db_path)
    scanned = 0
    updated = 0
    skipped = 0
    unknown = 0

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        for row in rows:
            if args.limit and updated >= args.limit:
                break
            scanned += 1
            task = dict(row)
            submit_config = _load_submit_config(task.get("submit_config_json"))
            existing_market = app._normalize_market(submit_config.get("market"))
            if existing_market:
                skipped += 1
                continue

            market = _task_market(task)
            if not market:
                unknown += 1
                continue

            submit_config["market"] = market
            payload = json.dumps(submit_config, ensure_ascii=False)
            if not args.dry_run:
                conn.execute(
                    "UPDATE tasks SET submit_config_json = ? WHERE task_id = ?",
                    (payload, task["task_id"]),
                )
            updated += 1

        if not args.dry_run:
            conn.commit()

    print(
        json.dumps(
            {
                "db": str(db_path),
                "dry_run": args.dry_run,
                "scanned": scanned,
                "updated": updated,
                "skipped": skipped,
                "unknown": unknown,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
