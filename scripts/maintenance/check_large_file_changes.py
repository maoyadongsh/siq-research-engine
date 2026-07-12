#!/usr/bin/env python3
"""Fail on newly changed large/binary artifacts without scanning old debt."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_IMAGE_MAX_BYTES = 1 * 1024 * 1024
RUNTIME_PREFIXES = (
    "artifacts/",
    "data/",
    "dist/",
    "node_modules/",
    "playwright-report/",
    "runtimes/",
    "runtime/",
    "test-results/",
    "var/",
)
RUNTIME_SOURCE_ALLOWLIST_BASENAMES = {".gitkeep", "README.md"}
LOCAL_REVIEW_PREFIXES = (
    ".superpowers/",
)
BLOCKED_SUFFIXES = {
    ".7z",
    ".avi",
    ".db",
    ".dump",
    ".gz",
    ".mkv",
    ".mov",
    ".mp4",
    ".onnx",
    ".parquet",
    ".pt",
    ".pth",
    ".rar",
    ".sqlite",
    ".sqlite3",
    ".backup",
    ".bak",
    ".tar",
    ".tgz",
    ".webm",
    ".zip",
}
IMAGE_SUFFIXES = {".avif", ".gif", ".jpeg", ".jpg", ".png", ".webp"}


@dataclass(frozen=True)
class LargeFileChangeFinding:
    code: str
    path: str
    detail: str
    size_bytes: int | None = None


def normalized_repo_path(path: str | Path) -> str:
    text = str(path).replace("\\", "/").strip()
    while text.startswith("./"):
        text = text[2:]
    return text


def runtime_artifact_path(path: str) -> bool:
    normalized = normalized_repo_path(path)
    return bool(normalized) and any(normalized.startswith(prefix) for prefix in RUNTIME_PREFIXES)


def runtime_source_allowlisted(path: str) -> bool:
    normalized = normalized_repo_path(path)
    return runtime_artifact_path(normalized) and Path(normalized).name in RUNTIME_SOURCE_ALLOWLIST_BASENAMES


def _git_lines(repo_root: Path, args: list[str]) -> list[str]:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def changed_paths(repo_root: Path, *, base_ref: str | None = None, include_untracked: bool = True) -> list[str]:
    repo_root = repo_root.resolve()
    paths: list[str] = []
    if base_ref:
        paths.extend(_git_lines(repo_root, ["diff", "--name-only", "--diff-filter=ACMRT", f"{base_ref}...HEAD", "--"]))
    else:
        paths.extend(_git_lines(repo_root, ["diff", "--name-only", "--diff-filter=ACMRT", "HEAD", "--"]))
        if include_untracked:
            paths.extend(_git_lines(repo_root, ["ls-files", "--others", "--exclude-standard"]))
    seen: list[str] = []
    for path in paths:
        normalized = normalized_repo_path(path)
        if normalized and normalized not in seen:
            seen.append(normalized)
    return seen


def finding_for_path(
    repo_root: Path,
    path: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    image_max_bytes: int = DEFAULT_IMAGE_MAX_BYTES,
) -> LargeFileChangeFinding | None:
    normalized = normalized_repo_path(path)
    if not normalized:
        return None
    full_path = (repo_root / normalized).resolve()
    if not full_path.is_file():
        return None
    if runtime_artifact_path(normalized):
        if runtime_source_allowlisted(normalized):
            return None
        return LargeFileChangeFinding(
            code="tracked_runtime_artifact_changed",
            path=normalized,
            detail="tracked runtime artifacts must stay out of source commits",
            size_bytes=full_path.stat().st_size,
        )
    if any(normalized.startswith(prefix) for prefix in LOCAL_REVIEW_PREFIXES):
        return LargeFileChangeFinding(
            code="local_review_artifact_changed",
            path=normalized,
            detail="local review artifacts must stay out of source commits",
        )
    suffix = full_path.suffix.lower()
    size = full_path.stat().st_size
    if suffix in BLOCKED_SUFFIXES:
        return LargeFileChangeFinding(
            code="blocked_binary_artifact_changed",
            path=normalized,
            detail=f"{suffix} artifacts should live in data/artifacts/object storage, not source history",
            size_bytes=size,
        )
    if suffix in IMAGE_SUFFIXES and size > image_max_bytes:
        return LargeFileChangeFinding(
            code="large_image_artifact_changed",
            path=normalized,
            detail=f"image exceeds {image_max_bytes} bytes",
            size_bytes=size,
        )
    if size > max_bytes:
        return LargeFileChangeFinding(
            code="large_file_changed",
            path=normalized,
            detail=f"file exceeds {max_bytes} bytes",
            size_bytes=size,
        )
    return None


def check_large_file_changes(
    repo_root: Path,
    *,
    paths: Iterable[str] | None = None,
    base_ref: str | None = None,
    include_untracked: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    image_max_bytes: int = DEFAULT_IMAGE_MAX_BYTES,
) -> list[LargeFileChangeFinding]:
    repo_root = repo_root.resolve()
    candidates = list(paths) if paths is not None else changed_paths(
        repo_root,
        base_ref=base_ref,
        include_untracked=include_untracked,
    )
    findings: list[LargeFileChangeFinding] = []
    for path in candidates:
        finding = finding_for_path(
            repo_root,
            path,
            max_bytes=max_bytes,
            image_max_bytes=image_max_bytes,
        )
        if finding is not None:
            findings.append(finding)
    return findings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail on newly changed large or binary artifacts.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--base-ref", default=None, help="Compare base...HEAD instead of local dirty tree.")
    parser.add_argument("--path", action="append", dest="paths", help="Explicit changed path to inspect; repeatable.")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--image-max-bytes", type=int, default=DEFAULT_IMAGE_MAX_BYTES)
    parser.add_argument("--no-untracked", action="store_true", help="Ignore untracked files in local dirty-tree mode.")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    findings = check_large_file_changes(
        args.repo_root,
        paths=args.paths,
        base_ref=args.base_ref,
        include_untracked=not args.no_untracked,
        max_bytes=args.max_bytes,
        image_max_bytes=args.image_max_bytes,
    )
    passed = not findings
    if args.json:
        print(json.dumps({"passed": passed, "findings": [asdict(item) for item in findings]}, ensure_ascii=False, indent=2))
    elif passed:
        print("PASS large-file changed-file gate")
    else:
        print("FAIL large-file changed-file gate")
        for item in findings:
            size = "" if item.size_bytes is None else f" ({item.size_bytes} bytes)"
            print(f"- {item.path}: {item.code}{size}: {item.detail}")
    return 0 if passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
