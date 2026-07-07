#!/usr/bin/env python3
"""Report the largest source files without failing CI.

This is an observe-only guard for the P3 refactor track. It intentionally skips
runtime/data directories and reports warning levels instead of blocking builds.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]

DEFAULT_PRUNE_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".turbo",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "coverage",
    "data",
    "dist",
    "node_modules",
    "playwright-report",
    "runtimes",
    "target",
    "test-results",
    "var",
    "venv",
}

DEFAULT_EXCLUDE_NAMES = {
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
}
DEFAULT_EXCLUDE_PREFIXES = (
    "db/imports/",
    "docs/architecture/",
)

SOURCE_SUFFIXES = {
    ".cjs",
    ".css",
    ".cts",
    ".html",
    ".js",
    ".json",
    ".jsx",
    ".md",
    ".mjs",
    ".mts",
    ".py",
    ".sh",
    ".sql",
    ".tsx",
    ".ts",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class LargeFileRecord:
    path: str
    line_count: int
    level: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Observe largest source files without failing the run.")
    parser.add_argument("--root", default=str(REPO_ROOT), help="Repository root to scan.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of files to report.")
    parser.add_argument("--warning-lines", type=int, default=2500, help="Line count that marks a warning.")
    parser.add_argument("--report-lines", type=int, default=4000, help="Line count that marks a report item.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser.parse_args()


def should_prune(path: Path, root: Path, prune_dirs: set[str] = DEFAULT_PRUNE_DIRS) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in prune_dirs for part in relative.parts)


def should_skip_source_file(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return True
    return path.name in DEFAULT_EXCLUDE_NAMES or any(relative.startswith(prefix) for prefix in DEFAULT_EXCLUDE_PREFIXES)


def iter_source_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_dir() or should_prune(path, root):
            continue
        if path.is_file() and path.suffix.lower() in SOURCE_SUFFIXES and not should_skip_source_file(path, root):
            yield path


def count_lines(path: Path) -> int:
    try:
        with path.open("r", encoding="utf-8", errors="ignore") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def level_for(line_count: int, *, warning_lines: int, report_lines: int) -> str:
    if line_count >= report_lines:
        return "report"
    if line_count >= warning_lines:
        return "warning"
    return "ok"


def observe_large_files(
    root: Path,
    *,
    limit: int = 20,
    warning_lines: int = 2500,
    report_lines: int = 4000,
) -> list[LargeFileRecord]:
    root = root.resolve()
    records: list[LargeFileRecord] = []
    for path in iter_source_files(root):
        line_count = count_lines(path)
        records.append(
            LargeFileRecord(
                path=path.relative_to(root).as_posix(),
                line_count=line_count,
                level=level_for(line_count, warning_lines=warning_lines, report_lines=report_lines),
            )
        )
    records.sort(key=lambda item: (-item.line_count, item.path))
    return records[: max(0, limit)]


def main() -> int:
    args = parse_args()
    root = Path(args.root)
    records = observe_large_files(
        root,
        limit=args.limit,
        warning_lines=args.warning_lines,
        report_lines=args.report_lines,
    )
    if args.json:
        print(json.dumps({"records": [asdict(item) for item in records]}, ensure_ascii=False, indent=2))
        return 0
    print("Largest source files (observe only):")
    for item in records:
        print(f"{item.line_count:>6} {item.level:<7} {item.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
