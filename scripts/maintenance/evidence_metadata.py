"""Small, dependency-free evidence envelope shared by maintenance commands."""

from __future__ import annotations

import hashlib
import subprocess
import time
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_evidence(repo_root: Path) -> tuple[str, bool, dict[str, Any]]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=normal"],
            cwd=repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown", True, {"available": False, "changed_paths": None}
    if commit.returncode != 0 or status.returncode != 0:
        return "unknown", True, {"available": False, "changed_paths": None}

    lines = [line for line in status.stdout.splitlines() if line]
    untracked = sum(1 for line in lines if line.startswith("??"))
    return (
        commit.stdout.strip(),
        bool(lines),
        {
            "available": True,
            "changed_paths": len(lines),
            "tracked_changes": len(lines) - untracked,
            "untracked_changes": untracked,
        },
    )


def _artifact_key(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return f"<external>/{path.name}"


def artifact_checksums(
    artifacts: Iterable[Path] | Mapping[str, Path],
    *,
    repo_root: Path,
) -> dict[str, str]:
    entries = (
        [(str(key), Path(path)) for key, path in artifacts.items()]
        if isinstance(artifacts, Mapping)
        else [(_artifact_key(Path(path), repo_root), Path(path)) for path in artifacts]
    )
    checksums: dict[str, str] = {}
    for requested_key, path in sorted(entries, key=lambda entry: (entry[0], entry[1].as_posix())):
        if not path.is_file():
            continue
        key = requested_key
        suffix = 2
        while key in checksums:
            key = f"{requested_key}#{suffix}"
            suffix += 1
        checksums[key] = sha256_file(path)
    return checksums


def attach_evidence_metadata(
    report: dict[str, Any],
    *,
    repo_root: Path,
    task_id: str,
    environment_profile: str,
    command: str,
    result: str,
    failures: list[Any],
    started_at: float,
    artifacts: Iterable[Path] | Mapping[str, Path],
) -> dict[str, Any]:
    base_commit, worktree_dirty, worktree_summary = _git_evidence(repo_root)
    checksums = artifact_checksums(artifacts, repo_root=repo_root)
    schema_version = report.get("schema_version")
    domain_fields = {key: value for key, value in report.items() if key != "schema_version"}
    return {
        "schema_version": schema_version,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "base_commit": base_commit,
        "worktree_dirty": worktree_dirty,
        "worktree_summary": worktree_summary,
        "task_id": task_id,
        "environment_profile": environment_profile,
        "command": command,
        "result": result,
        "duration_seconds": round(time.monotonic() - started_at, 6),
        "failures": failures,
        "artifact_checksums": checksums,
        **domain_fields,
    }
