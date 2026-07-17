#!/usr/bin/env python3
"""Fail closed when a candidate sandbox mount contains credentials or host state."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

MAX_SCAN_BYTES = 1024 * 1024
MAX_FINDINGS = 200
FORBIDDEN_PREFIXES = {
    PurePosixPath(".git"),
    PurePosixPath("data/hermes/home/state-snapshots"),
    PurePosixPath("data/postgres/raw-container-data"),
    PurePosixPath("var/meetings/hermes"),
    PurePosixPath("var/openshell"),
}
FORBIDDEN_DIRECTORY_NAMES = {".aws", ".gnupg", ".ssh", ".venv", "node_modules", "__pycache__"}
FORBIDDEN_FILENAMES = {
    ".env",
    "auth.json",
    "credentials",
    "credentials.json",
    "docker.sock",
    "id_ed25519",
    "id_rsa",
}
FORBIDDEN_SUFFIXES = {".env", ".key", ".p12", ".pfx"}
PRIVATE_KEY_BLOCK = re.compile(
    rb"-----BEGIN (?P<label>[A-Z0-9 ]*PRIVATE KEY)-----\r?\n"
    rb"(?P<body>[A-Za-z0-9+/=\r\n]{32,})"
    rb"-----END (?P=label)-----"
)


@dataclass(frozen=True)
class Finding:
    path: str
    code: str


def _under_prefix(path: PurePosixPath) -> bool:
    return any(path == prefix or prefix in path.parents for prefix in FORBIDDEN_PREFIXES)


def _credential_name(name: str) -> bool:
    lowered = name.lower()
    return lowered in FORBIDDEN_FILENAMES or any(lowered.endswith(suffix) for suffix in FORBIDDEN_SUFFIXES)


def scan_mount_root(root: Path, *, max_findings: int = MAX_FINDINGS) -> list[Finding]:
    root = root.absolute()
    if root.is_symlink():
        return [Finding(".", "mount_root_symlink")]
    if not root.is_dir():
        return [Finding(".", "mount_root_not_directory")]

    findings: list[Finding] = []

    def add(relative: PurePosixPath, code: str) -> None:
        if len(findings) < max_findings:
            findings.append(Finding(relative.as_posix(), code))

    def onerror(error: OSError) -> None:
        try:
            relative = PurePosixPath(Path(error.filename or root).relative_to(root).as_posix())
        except ValueError:
            relative = PurePosixPath(".")
        add(relative, "unreadable_path")

    for current_text, directories, files in os.walk(root, topdown=True, followlinks=False, onerror=onerror):
        current = Path(current_text)
        relative_current = PurePosixPath(current.relative_to(root).as_posix())
        if relative_current != PurePosixPath(".") and _under_prefix(relative_current):
            add(relative_current, "forbidden_mount_subtree")
            directories[:] = []
            continue

        kept_directories: list[str] = []
        for name in sorted(directories):
            candidate = current / name
            relative = PurePosixPath(candidate.relative_to(root).as_posix())
            if candidate.is_symlink():
                add(relative, "symlink_not_allowed")
            elif _under_prefix(relative):
                add(relative, "forbidden_mount_subtree")
            elif name in FORBIDDEN_DIRECTORY_NAMES:
                add(relative, "forbidden_mount_subtree")
            else:
                kept_directories.append(name)
        directories[:] = kept_directories

        for name in sorted(files):
            candidate = current / name
            relative = PurePosixPath(candidate.relative_to(root).as_posix())
            try:
                mode = candidate.lstat().st_mode
            except OSError:
                add(relative, "unreadable_path")
                continue
            if stat.S_ISLNK(mode):
                add(relative, "symlink_not_allowed")
                continue
            if not stat.S_ISREG(mode):
                add(relative, "non_regular_file")
                continue
            if _credential_name(name):
                add(relative, "credential_path_not_allowed")
                continue
            try:
                with candidate.open("rb") as handle:
                    prefix = handle.read(MAX_SCAN_BYTES)
            except OSError:
                add(relative, "unreadable_path")
                continue
            if PRIVATE_KEY_BLOCK.search(prefix):
                add(relative, "private_key_material")

        if len(findings) >= max_findings:
            break
    return sorted(set(findings), key=lambda item: (item.path, item.code))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mount-root", type=Path, required=True)
    parser.add_argument("--max-findings", type=int, default=MAX_FINDINGS)
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_findings <= 0:
        _parser().error("--max-findings must be positive")
    findings = scan_mount_root(args.mount_root, max_findings=args.max_findings)
    result = {"ok": not findings, "finding_count": len(findings), "findings": [asdict(item) for item in findings]}
    if args.as_json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    elif findings:
        for finding in findings:
            print(f"{finding.path}: {finding.code}")
    else:
        print("OpenShell candidate mount scan: PASS")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
