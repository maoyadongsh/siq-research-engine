#!/usr/bin/env python3
"""Print a read-only migration plan for SIQ local runtime paths."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]


def _path_arg(value: str) -> Path:
    return Path(value).expanduser().resolve()


def _dir_size(path: Path) -> int | None:
    if not path.exists():
        return None
    total = 0
    for item in path.rglob("*"):
        try:
            if item.is_file() or item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _human_size(size: int | None) -> str:
    if size is None:
        return "missing"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def _entries(source_root: Path, target_state_root: Path) -> list[dict[str, Any]]:
    data_target = target_state_root / "data"
    return [
        {
            "name": "api backend state",
            "source": source_root / "backend",
            "target": data_target / "backend",
            "env": "SIQ_DATA_ROOT/backend",
        },
        {
            "name": "wiki facts",
            "source": source_root / "wiki",
            "target": data_target / "wiki",
            "env": "SIQ_WIKI_ROOT",
        },
        {
            "name": "market report downloads",
            "source": source_root / "market-report-finder" / "downloads",
            "target": data_target / "market-report-finder" / "downloads",
            "env": "SIQ_REPORT_DOWNLOADS_ROOT",
        },
        {
            "name": "PDF parser runtime",
            "source": source_root / "pdf-parser",
            "target": data_target / "pdf-parser",
            "env": "SIQ_PDF2MD_DATA_DIR",
        },
        {
            "name": "document parser runtime",
            "source": source_root / "document-parser",
            "target": data_target / "document-parser",
            "env": "SIQ_DOCUMENT_PARSE_DATA_DIR",
        },
        {
            "name": "Hermes runtime home",
            "source": source_root / "hermes" / "home",
            "target": data_target / "hermes" / "home",
            "env": "SIQ_HERMES_HOME",
        },
        {
            "name": "Postgres bind data",
            "source": source_root / "postgres",
            "target": data_target / "postgres",
            "env": "SIQ_POSTGRES_DATA_VOLUME or external DB",
        },
    ]


def _plan(source_root: Path, target_state_root: Path, *, include_size: bool = False) -> dict[str, Any]:
    entries = []
    for entry in _entries(source_root, target_state_root):
        source = entry["source"]
        target = entry["target"]
        size = _dir_size(source) if include_size else None
        entries.append(
            {
                **entry,
                "source": str(source),
                "target": str(target),
                "exists": source.exists(),
                "size_bytes": size,
                "size": _human_size(size) if include_size else "not calculated",
            }
        )
    return {
        "schema_version": "siq_runtime_path_migration_plan_v1",
        "read_only": True,
        "repo_root": str(REPO_ROOT),
        "source_data_root": str(source_root),
        "target_local_state_root": str(target_state_root),
        "target_data_root": str(target_state_root / "data"),
        "target_runtime_root": str(target_state_root / "var"),
        "target_artifacts_root": str(target_state_root / "artifacts"),
        "entries": entries,
        "notes": [
            "This command only reports paths; it does not create, move, copy, or delete files.",
            "Set SIQ_LOCAL_STATE_ROOT first, then move data manually during a maintenance window.",
            "Keep the old data/ tree until services have been verified against the new paths.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan SIQ local runtime path migration without moving files.")
    parser.add_argument(
        "--source-data-root",
        type=_path_arg,
        default=_path_arg(os.environ.get("SIQ_DATA_ROOT", str(REPO_ROOT / "data"))),
        help="Current data root to inspect. Defaults to SIQ_DATA_ROOT or repo data/.",
    )
    parser.add_argument(
        "--target-local-state-root",
        type=_path_arg,
        default=_path_arg(os.environ.get("SIQ_LOCAL_STATE_ROOT", "/var/lib/siq-research-engine")),
        help="Recommended target local state root.",
    )
    parser.add_argument("--json", action="store_true", help="Print the migration plan as JSON.")
    parser.add_argument("--include-size", action="store_true", help="Calculate directory sizes; can be slow on large data.")
    args = parser.parse_args()

    plan = _plan(args.source_data_root, args.target_local_state_root, include_size=args.include_size)
    if args.json:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return

    print("SIQ runtime path migration plan (read-only)")
    print(f"repo root: {plan['repo_root']}")
    print(f"source data root: {plan['source_data_root']}")
    print(f"target local state root: {plan['target_local_state_root']}")
    print()
    for entry in plan["entries"]:
        status = "exists" if entry["exists"] else "missing"
        print(f"- {entry['name']} [{status}, {entry['size']}]")
        print(f"  source: {entry['source']}")
        print(f"  target: {entry['target']}")
        print(f"  env: {entry['env']}")
    print()
    for note in plan["notes"]:
        print(f"NOTE: {note}")


if __name__ == "__main__":
    main()
