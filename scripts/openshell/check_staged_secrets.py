#!/usr/bin/env python3
"""Scan the exact Git index blob snapshot with a pinned gitleaks release."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, Sequence

GITLEAKS_VERSION = "8.24.2"
GITLEAKS_IMAGE = f"ghcr.io/gitleaks/gitleaks:v{GITLEAKS_VERSION}"
TRUSTED_CONFIG = br"""[extend]
useDefault = true

[allowlist]
description = "Known sanitized secondary-market smoke evidence"
paths = ['(?:^|/)artifacts/secondary-market-multi-market/real-smoke\.sanitized\.json$']
"""
VERSION_RE = re.compile(r"(?<![0-9])(\d+\.\d+\.\d+)(?![0-9])")
BLOB_MODES = frozenset({"100644", "100755", "120000"})
GITLINK_MODE = "160000"


class StagedSecretScanError(RuntimeError):
    """A stable failure whose text never contains scanner or blob output."""


@dataclass(frozen=True)
class Scanner:
    kind: Literal["local", "docker"]
    executable: str


@dataclass(frozen=True)
class SnapshotStats:
    index_entries: int
    blob_entries: int
    gitlinks: int


def _run(
    command: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: int = 120,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=env,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise StagedSecretScanError("scanner_execution_failed") from exc


def _git(repo_root: Path, *args: str) -> bytes:
    completed = _run(["git", "-C", str(repo_root), *args], timeout=120)
    if completed.returncode != 0:
        raise StagedSecretScanError("git_index_read_failed")
    return completed.stdout


def resolve_repo_root(candidate: Path) -> Path:
    raw = _git(candidate, "rev-parse", "--show-toplevel")
    try:
        root = Path(os.fsdecode(raw.rstrip(b"\n"))).resolve(strict=True)
    except (OSError, ValueError) as exc:
        raise StagedSecretScanError("git_repo_root_invalid") from exc
    if not root.is_dir():
        raise StagedSecretScanError("git_repo_root_invalid")
    return root


def _parse_index(raw: bytes) -> list[tuple[str, str, bytes]]:
    entries: list[tuple[str, str, bytes]] = []
    for record in raw.split(b"\0"):
        if not record:
            continue
        try:
            metadata, path = record.split(b"\t", 1)
            mode_raw, oid_raw, stage_raw = metadata.split(b" ", 2)
            mode = mode_raw.decode("ascii")
            oid = oid_raw.decode("ascii")
            stage = stage_raw.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise StagedSecretScanError("git_index_record_invalid") from exc
        if stage != "0":
            raise StagedSecretScanError("git_index_unmerged")
        if mode not in BLOB_MODES and mode != GITLINK_MODE:
            raise StagedSecretScanError("git_index_mode_unsupported")
        pure = PurePosixPath(os.fsdecode(path))
        if (
            not path
            or path.startswith(b"/")
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise StagedSecretScanError("git_index_path_invalid")
        entries.append((mode, oid, path))
    return entries


def _private_directory(path: Path) -> None:
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if stat.S_IMODE(path.stat().st_mode) != 0o700:
        path.chmod(0o700)


def _write_private_regular(path: Path, content: bytes) -> None:
    _private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def materialize_index(repo_root: Path, destination: Path) -> SnapshotStats:
    """Materialize stage-zero blobs as regular files without reading the worktree."""

    before = _git(repo_root, "ls-files", "--stage", "-z")
    entries = _parse_index(before)
    blob_entries = 0
    gitlinks = 0
    for mode, oid, raw_path in entries:
        if mode == GITLINK_MODE:
            gitlinks += 1
            continue
        content = _git(repo_root, "cat-file", "blob", oid)
        _write_private_regular(destination / os.fsdecode(raw_path), content)
        blob_entries += 1
    after = _git(repo_root, "ls-files", "--stage", "-z")
    if before != after:
        raise StagedSecretScanError("git_index_changed_during_snapshot")
    return SnapshotStats(len(entries), blob_entries, gitlinks)


def _reported_version(completed: subprocess.CompletedProcess[bytes]) -> str | None:
    if completed.returncode != 0:
        return None
    text = (completed.stdout + b"\n" + completed.stderr).decode("utf-8", errors="replace")
    versions = VERSION_RE.findall(text)
    if len(versions) != 1:
        return None
    return versions[0]


def _local_scanner() -> Scanner | None:
    executable = shutil.which("gitleaks")
    if executable is None:
        return None
    completed = _run([executable, "version"], timeout=30)
    if _reported_version(completed) != GITLEAKS_VERSION:
        return None
    return Scanner("local", executable)


def _docker_scanner() -> Scanner | None:
    executable = shutil.which("docker")
    if executable is None:
        return None
    server = _run([executable, "version", "--format", "{{.Server.Version}}"], timeout=30)
    if server.returncode != 0:
        return None
    version = _run(
        [executable, "run", "--rm", "--network", "none", GITLEAKS_IMAGE, "version"],
        timeout=300,
    )
    if _reported_version(version) != GITLEAKS_VERSION:
        return None
    return Scanner("docker", executable)


def select_scanner(preference: Literal["auto", "local", "docker"]) -> Scanner:
    candidates = {
        "local": (_local_scanner,),
        "docker": (_docker_scanner,),
        "auto": (_local_scanner, _docker_scanner),
    }[preference]
    for candidate in candidates:
        try:
            scanner = candidate()
        except StagedSecretScanError:
            scanner = None
        if scanner is not None:
            return scanner
    raise StagedSecretScanError("pinned_gitleaks_unavailable")


def _scan_environment() -> dict[str, str]:
    environment = os.environ.copy()
    for key in tuple(environment):
        if key.startswith("GITLEAKS_"):
            environment.pop(key, None)
    return environment


def scan_snapshot(scanner: Scanner, snapshot: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="siq-gitleaks-config-") as temporary:
        trusted_root = Path(temporary)
        config_path = trusted_root / "gitleaks.toml"
        ignore_path = trusted_root / "gitleaksignore"
        _write_private_regular(config_path, TRUSTED_CONFIG)
        _write_private_regular(ignore_path, b"")
        if scanner.kind == "local":
            config_flag = f"--config={config_path}"
            ignore_flag = f"--gitleaks-ignore-path={ignore_path}"
        else:
            config_flag = "--config=/siq-gitleaks-config/gitleaks.toml"
            ignore_flag = "--gitleaks-ignore-path=/siq-gitleaks-config/gitleaksignore"
        flags = [
            "detect",
            "--source=.",
            "--no-git",
            config_flag,
            ignore_flag,
            "--redact=100",
            "--no-banner",
            "--no-color",
            "--log-level=error",
            "--exit-code=1",
        ]
        if scanner.kind == "local":
            command = [scanner.executable, *flags]
        else:
            command = [
                scanner.executable,
                "run",
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=bind,src={snapshot},dst=/repo,readonly",
                "--mount",
                f"type=bind,src={trusted_root},dst=/siq-gitleaks-config,readonly",
                "--workdir",
                "/repo",
                GITLEAKS_IMAGE,
                *flags,
            ]
        completed = _run(command, cwd=snapshot, timeout=900, env=_scan_environment())
    if completed.returncode not in {0, 1}:
        raise StagedSecretScanError("gitleaks_scan_failed")
    return completed.returncode


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--scanner", choices=("auto", "local", "docker"), default="auto")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        repo_root = resolve_repo_root(args.repo_root)
        scanner = select_scanner(args.scanner)
        with tempfile.TemporaryDirectory(prefix="siq-openshell-index-") as temporary:
            snapshot = Path(temporary) / "snapshot"
            _private_directory(snapshot)
            stats = materialize_index(repo_root, snapshot)
            result = scan_snapshot(scanner, snapshot)
    except StagedSecretScanError as exc:
        print(
            f"staged_secret_scan=failed reason={exc} required_gitleaks={GITLEAKS_VERSION}",
            file=sys.stderr,
        )
        return 2
    if result == 1:
        print(
            "staged_secret_scan=failed reason=potential_secret_detected "
            f"scanner={scanner.kind} scanner_output=suppressed",
            file=sys.stderr,
        )
        return 1
    print(
        "staged_secret_scan=passed "
        f"scanner={scanner.kind} gitleaks={GITLEAKS_VERSION} "
        f"index_entries={stats.index_entries} blob_entries={stats.blob_entries} "
        f"gitlinks={stats.gitlinks}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
