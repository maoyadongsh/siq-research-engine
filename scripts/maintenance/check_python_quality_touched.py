#!/usr/bin/env python3
"""Run lightweight Python quality checks for touched files."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_REF = "origin/main"
DEFAULT_EXCLUDE_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "artifacts",
    "data",
    "node_modules",
    "runtimes",
    "var",
    "venv",
}


@dataclass(frozen=True)
class QualityResult:
    status: str
    files: list[str]
    commands: list[list[str]]
    messages: list[str]
    stdout: str = ""
    stderr: str = ""


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _is_excluded(path: Path) -> bool:
    return any(part in DEFAULT_EXCLUDE_PARTS for part in path.parts)


def _is_python_file(path: Path) -> bool:
    return path.suffix == ".py" and not _is_excluded(path)


def _run_git(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def changed_files(repo_root: Path, *, base_ref: str = DEFAULT_BASE_REF, include_untracked: bool = True) -> list[Path]:
    paths: set[Path] = set()
    diff = _run_git(repo_root, ["diff", "--name-only", "--diff-filter=ACMRT", base_ref, "--"])
    if diff.returncode == 0:
        paths.update(repo_root / line for line in diff.stdout.splitlines() if line.strip())
    else:
        staged = _run_git(repo_root, ["diff", "--name-only", "--diff-filter=ACMRT", "--cached", "--"])
        unstaged = _run_git(repo_root, ["diff", "--name-only", "--diff-filter=ACMRT", "--"])
        for result in (staged, unstaged):
            if result.returncode == 0:
                paths.update(repo_root / line for line in result.stdout.splitlines() if line.strip())

    if include_untracked:
        untracked = _run_git(repo_root, ["ls-files", "--others", "--exclude-standard"])
        if untracked.returncode == 0:
            paths.update(repo_root / line for line in untracked.stdout.splitlines() if line.strip())

    return sorted(path for path in paths if _is_python_file(path))


def select_python_files(repo_root: Path, files: list[Path] | None, *, base_ref: str, include_untracked: bool) -> list[str]:
    candidates = files if files is not None else changed_files(repo_root, base_ref=base_ref, include_untracked=include_untracked)
    selected: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        path = candidate if candidate.is_absolute() else repo_root / candidate
        if not path.exists() or not path.is_file() or not _is_python_file(path):
            continue
        relative = _repo_relative(path, repo_root)
        if relative not in seen:
            selected.append(relative)
            seen.add(relative)
    return sorted(selected)


def run_quality_checks(
    repo_root: Path,
    *,
    files: list[Path] | None = None,
    base_ref: str = DEFAULT_BASE_REF,
    include_untracked: bool = True,
    require_ruff: bool = False,
) -> tuple[int, QualityResult]:
    repo_root = repo_root.resolve()
    selected = select_python_files(repo_root, files, base_ref=base_ref, include_untracked=include_untracked)
    messages: list[str] = []
    commands: list[list[str]] = []
    if not selected:
        return 0, QualityResult(status="skipped", files=[], commands=[], messages=["No touched Python files to check."])

    ruff = shutil.which("ruff")
    if not ruff:
        message = "ruff is not installed; touched-file Python quality check is advisory only."
        messages.append(message)
        status = "failed" if require_ruff else "advisory"
        return (1 if require_ruff else 0), QualityResult(status=status, files=selected, commands=[], messages=messages)

    command = [ruff, "check", *selected]
    commands.append(command)
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        messages.append(f"ruff check failed with exit code {completed.returncode}.")
        return completed.returncode, QualityResult(
            status="failed",
            files=selected,
            commands=commands,
            messages=messages,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
    messages.append("ruff check passed.")
    return 0, QualityResult(
        status="passed",
        files=selected,
        commands=commands,
        messages=messages,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run lightweight quality checks for touched Python files.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--base-ref", default=DEFAULT_BASE_REF)
    parser.add_argument("--no-untracked", action="store_true", help="Ignore untracked files when discovering touched files.")
    parser.add_argument("--require-ruff", action="store_true", help="Fail if ruff is not installed.")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary.")
    parser.add_argument("files", nargs="*", type=Path, help="Explicit files to check instead of git changed files.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    exit_code, result = run_quality_checks(
        args.repo_root,
        files=args.files or None,
        base_ref=args.base_ref,
        include_untracked=not args.no_untracked,
        require_ruff=args.require_ruff,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(f"{result.status.upper()} touched Python quality check")
        for message in result.messages:
            print(message)
        for command in result.commands:
            print("+ " + " ".join(command))
        if result.files:
            print("Files:")
            for path in result.files:
                print(f"- {path}")
        if result.stdout:
            print(result.stdout.rstrip())
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
