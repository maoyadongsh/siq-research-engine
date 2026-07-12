#!/usr/bin/env python3
"""Fail when touched Python files add Ruff diagnostic fingerprints."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASE_REF = "HEAD"
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
class FindingDelta:
    fingerprint: str
    path: str
    code: str
    message: str
    source: str
    baseline_count: int
    current_count: int
    new_count: int


@dataclass(frozen=True)
class QualityResult:
    status: str
    files: list[str]
    commands: list[list[str]]
    messages: list[str]
    baseline_ref: str | None = None
    ruff_version: str | None = None
    baseline_finding_count: int = 0
    current_finding_count: int = 0
    new_finding_count: int = 0
    new_findings: list[FindingDelta] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True)
class RuffFinding:
    fingerprint: str
    path: str
    code: str
    message: str
    source: str


class GitBaselineError(RuntimeError):
    """Raised when the requested Git baseline cannot be inspected."""


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


def resolve_base_ref(repo_root: Path, base_ref: str) -> str:
    resolved = _run_git(repo_root, ["rev-parse", "--verify", "--quiet", f"{base_ref}^{{commit}}"])
    if resolved.returncode != 0 or not resolved.stdout.strip():
        detail = resolved.stderr.strip() or "reference does not resolve to a commit"
        raise GitBaselineError(f"Cannot resolve Ruff baseline ref {base_ref!r}: {detail}.")
    return resolved.stdout.strip()


def changed_files(repo_root: Path, *, base_ref: str = DEFAULT_BASE_REF, include_untracked: bool = True) -> list[Path]:
    resolve_base_ref(repo_root, base_ref)
    diff = _run_git(repo_root, ["diff", "--name-only", "--diff-filter=ACMRT", base_ref, "--"])
    if diff.returncode != 0:
        detail = diff.stderr.strip() or "git diff failed"
        raise GitBaselineError(f"Cannot discover files changed from {base_ref!r}: {detail}.")

    paths = {repo_root / line for line in diff.stdout.splitlines() if line.strip()}
    if include_untracked:
        untracked = _run_git(repo_root, ["ls-files", "--others", "--exclude-standard"])
        if untracked.returncode != 0:
            detail = untracked.stderr.strip() or "git ls-files failed"
            raise GitBaselineError(f"Cannot discover untracked files: {detail}.")
        paths.update(repo_root / line for line in untracked.stdout.splitlines() if line.strip())

    return sorted(path for path in paths if _is_python_file(path))


def select_python_files(
    repo_root: Path,
    files: list[Path] | None,
    *,
    base_ref: str,
    include_untracked: bool,
) -> list[str]:
    candidates = files if files is not None else changed_files(
        repo_root,
        base_ref=base_ref,
        include_untracked=include_untracked,
    )
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


def _base_paths(repo_root: Path, base_ref: str) -> dict[str, str]:
    """Map current paths to their paths at base_ref, preserving pure renames."""
    diff = _run_git(repo_root, ["diff", "--name-status", "-z", "-M", base_ref, "--"])
    if diff.returncode != 0:
        detail = diff.stderr.strip() or "git diff failed"
        raise GitBaselineError(f"Cannot inspect renamed files from {base_ref!r}: {detail}.")

    fields = diff.stdout.split("\0")
    mapping: dict[str, str] = {}
    index = 0
    while index < len(fields) and fields[index]:
        status = fields[index]
        index += 1
        if status.startswith("R"):
            if index + 1 >= len(fields):
                raise GitBaselineError("Malformed NUL-delimited git rename output.")
            old_path, new_path = fields[index], fields[index + 1]
            index += 2
            mapping[new_path] = old_path
        else:
            if index >= len(fields):
                raise GitBaselineError("Malformed NUL-delimited git diff output.")
            path = fields[index]
            index += 1
            if not status.startswith("A"):
                mapping[path] = path
    return mapping


def _write_baseline_snapshot(repo_root: Path, base_ref: str, files: list[str], destination: Path) -> list[str]:
    base_paths = _base_paths(repo_root, base_ref)
    snapshot_files: list[str] = []
    for current_path in files:
        base_path = base_paths.get(current_path, current_path)
        exists = _run_git(repo_root, ["cat-file", "-e", f"{base_ref}:{base_path}"])
        if exists.returncode != 0:
            continue
        content = subprocess.run(
            ["git", "show", f"{base_ref}:{base_path}"],
            cwd=repo_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if content.returncode != 0:
            detail = content.stderr.decode("utf-8", errors="replace").strip() or "git show failed"
            raise GitBaselineError(f"Cannot read {base_path!r} from {base_ref!r}: {detail}.")
        target = destination / current_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content.stdout)
        snapshot_files.append(current_path)
    return snapshot_files


def _normalized_source(path: Path, row: int, end_row: int) -> str:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return ""
    if row < 1 or row > len(lines):
        return ""
    bounded_end = max(row, min(end_row, len(lines)))
    return "\n".join(" ".join(line.strip().split()) for line in lines[row - 1 : bounded_end])


def _fingerprint(*, path: str, code: str, message: str, source: str) -> str:
    canonical = json.dumps(
        {"path": path, "code": code, "message": message, "source": source},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _parse_ruff_output(stdout: str, *, source_root: Path) -> list[RuffFinding]:
    try:
        payload = json.loads(stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ValueError(f"ruff returned invalid JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise ValueError("ruff JSON output must be a list")

    findings: list[RuffFinding] = []
    for raw in payload:
        if not isinstance(raw, dict):
            raise ValueError("ruff JSON finding must be an object")
        filename = str(raw.get("filename") or "")
        raw_path = Path(filename)
        path = _repo_relative(raw_path if raw_path.is_absolute() else source_root / raw_path, source_root)
        raw_location = raw.get("location")
        raw_end_location = raw.get("end_location")
        location = raw_location if isinstance(raw_location, dict) else {}
        end_location = raw_end_location if isinstance(raw_end_location, dict) else {}
        row = int(location.get("row") or 0)
        end_row = int(end_location.get("row") or row)
        code = str(raw.get("code") or "UNKNOWN")
        message = str(raw.get("message") or "")
        source = _normalized_source(source_root / path, row, end_row)
        findings.append(
            RuffFinding(
                fingerprint=_fingerprint(path=path, code=code, message=message, source=source),
                path=path,
                code=code,
                message=message,
                source=source,
            )
        )
    return findings


def _ruff_version(ruff: str, repo_root: Path) -> tuple[str | None, str | None]:
    completed = subprocess.run(
        [ruff, "--version"],
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        return None, completed.stderr.strip() or "ruff --version failed"
    return completed.stdout.strip(), None


def _run_ruff(
    ruff: str,
    *,
    source_root: Path,
    config: Path,
    files: list[str],
) -> tuple[list[str], subprocess.CompletedProcess[str], list[RuffFinding] | None, str | None]:
    command = [ruff, "check", "--config", str(config), "--output-format", "json", *files]
    completed = subprocess.run(
        command,
        cwd=source_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode not in {0, 1}:
        return command, completed, None, f"ruff check failed with exit code {completed.returncode}."
    try:
        findings = _parse_ruff_output(completed.stdout, source_root=source_root)
    except ValueError as exc:
        return command, completed, None, str(exc)
    return command, completed, findings, None


def _finding_deltas(current: list[RuffFinding], baseline: list[RuffFinding]) -> list[FindingDelta]:
    current_counts = Counter(finding.fingerprint for finding in current)
    baseline_counts = Counter(finding.fingerprint for finding in baseline)
    examples = {finding.fingerprint: finding for finding in current}
    deltas: list[FindingDelta] = []
    for fingerprint, current_count in sorted(current_counts.items()):
        baseline_count = baseline_counts[fingerprint]
        if current_count <= baseline_count:
            continue
        finding = examples[fingerprint]
        deltas.append(
            FindingDelta(
                fingerprint=fingerprint,
                path=finding.path,
                code=finding.code,
                message=finding.message,
                source=finding.source,
                baseline_count=baseline_count,
                current_count=current_count,
                new_count=current_count - baseline_count,
            )
        )
    return deltas


def run_quality_checks(
    repo_root: Path,
    *,
    files: list[Path] | None = None,
    base_ref: str = DEFAULT_BASE_REF,
    include_untracked: bool = True,
    require_ruff: bool = False,
) -> tuple[int, QualityResult]:
    repo_root = repo_root.resolve()
    try:
        resolved_base = resolve_base_ref(repo_root, base_ref)
        selected = select_python_files(
            repo_root,
            files,
            base_ref=resolved_base,
            include_untracked=include_untracked,
        )
    except GitBaselineError as exc:
        return 2, QualityResult(status="failed", files=[], commands=[], messages=[str(exc)], baseline_ref=base_ref)

    if not selected:
        return 0, QualityResult(
            status="skipped",
            files=[],
            commands=[],
            messages=["No touched Python files to check."],
            baseline_ref=resolved_base,
        )

    ruff = shutil.which("ruff")
    if not ruff:
        message = "ruff is not installed; install it or run with --require-ruff in CI."
        status = "failed" if require_ruff else "advisory"
        return (1 if require_ruff else 0), QualityResult(
            status=status,
            files=selected,
            commands=[],
            messages=[message],
            baseline_ref=resolved_base,
        )

    version, version_error = _ruff_version(ruff, repo_root)
    if version_error:
        return 2, QualityResult(
            status="failed",
            files=selected,
            commands=[[ruff, "--version"]],
            messages=[version_error],
            baseline_ref=resolved_base,
        )

    config = repo_root / "ruff.toml"
    if not config.is_file():
        return 2, QualityResult(
            status="failed",
            files=selected,
            commands=[],
            messages=[f"Ruff config not found: {config}."],
            baseline_ref=resolved_base,
            ruff_version=version,
        )

    current_command, current_run, current_findings, current_error = _run_ruff(
        ruff,
        source_root=repo_root,
        config=config,
        files=selected,
    )
    commands = [current_command]
    if current_error or current_findings is None:
        return 2, QualityResult(
            status="failed",
            files=selected,
            commands=commands,
            messages=[current_error or "ruff check failed."],
            baseline_ref=resolved_base,
            ruff_version=version,
            stdout=current_run.stdout,
            stderr=current_run.stderr,
        )

    with tempfile.TemporaryDirectory(prefix="siq-ruff-baseline-") as temp_dir:
        snapshot_root = Path(temp_dir)
        try:
            snapshot_files = _write_baseline_snapshot(repo_root, resolved_base, selected, snapshot_root)
        except GitBaselineError as exc:
            return 2, QualityResult(
                status="failed",
                files=selected,
                commands=commands,
                messages=[str(exc)],
                baseline_ref=resolved_base,
                ruff_version=version,
                current_finding_count=len(current_findings),
            )

        baseline_findings: list[RuffFinding] = []
        baseline_stdout = ""
        baseline_stderr = ""
        if snapshot_files:
            baseline_command, baseline_run, parsed_baseline, baseline_error = _run_ruff(
                ruff,
                source_root=snapshot_root,
                config=config,
                files=snapshot_files,
            )
            commands.append([*baseline_command[:6], "<git-baseline-snapshot>", *snapshot_files])
            baseline_stdout = baseline_run.stdout
            baseline_stderr = baseline_run.stderr
            if baseline_error or parsed_baseline is None:
                return 2, QualityResult(
                    status="failed",
                    files=selected,
                    commands=commands,
                    messages=[baseline_error or "Baseline ruff check failed."],
                    baseline_ref=resolved_base,
                    ruff_version=version,
                    current_finding_count=len(current_findings),
                    stdout=baseline_stdout,
                    stderr=baseline_stderr,
                )
            baseline_findings = parsed_baseline

    deltas = _finding_deltas(current_findings, baseline_findings)
    new_count = sum(delta.new_count for delta in deltas)
    if deltas:
        messages = [
            f"Ruff found {new_count} new diagnostic occurrence(s) across {len(deltas)} fingerprint(s).",
            "Historical diagnostics matching the Git baseline remain non-blocking.",
        ]
        return 1, QualityResult(
            status="failed",
            files=selected,
            commands=commands,
            messages=messages,
            baseline_ref=resolved_base,
            ruff_version=version,
            baseline_finding_count=len(baseline_findings),
            current_finding_count=len(current_findings),
            new_finding_count=new_count,
            new_findings=deltas,
            stdout=current_run.stdout,
            stderr=current_run.stderr,
        )

    return 0, QualityResult(
        status="passed",
        files=selected,
        commands=commands,
        messages=[
            "No new Ruff diagnostic fingerprints were introduced.",
            f"Matched {len(current_findings)} current diagnostic occurrence(s) against the Git baseline.",
        ],
        baseline_ref=resolved_base,
        ruff_version=version,
        baseline_finding_count=len(baseline_findings),
        current_finding_count=len(current_findings),
        stdout=current_run.stdout,
        stderr=current_run.stderr,
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fail only when touched Python files add Ruff diagnostic fingerprints relative to Git."
    )
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--base-ref", default=DEFAULT_BASE_REF)
    parser.add_argument("--no-untracked", action="store_true", help="Ignore untracked files when discovering touched files.")
    parser.add_argument("--require-ruff", action="store_true", help="Fail if ruff is not installed.")
    parser.add_argument("--json", action="store_true", help="Emit an auditable JSON fingerprint report.")
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
        print(f"{result.status.upper()} touched Python Ruff fingerprint gate")
        for message in result.messages:
            print(message)
        for command in result.commands:
            print("+ " + " ".join(command))
        if result.files:
            print("Files:")
            for path in result.files:
                print(f"- {path}")
        if result.new_findings:
            print("New fingerprints:")
            for finding in result.new_findings:
                print(f"- {finding.path}: {finding.code} {finding.message} (new={finding.new_count})")
        if result.stdout and result.new_finding_count:
            print(result.stdout.rstrip())
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
