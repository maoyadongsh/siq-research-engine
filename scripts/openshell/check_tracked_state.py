#!/usr/bin/env python3
"""Verify manifest-bound, sanitized OpenShell artifacts in the Git index."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

try:
    from scripts.openshell import sanitized_log_contract
    from scripts.openshell.check_sanitized_artifacts import Finding, scan_content
except ModuleNotFoundError:  # direct execution from the scripts/openshell directory
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import sanitized_log_contract
    from check_sanitized_artifacts import Finding, scan_content

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = "artifacts/openshell/tracked-artifacts.json"
MANIFEST_SCHEMA = "siq.openshell.tracked-artifacts.v1"
OPEN_SHELL_PREFIXES = ("artifacts/openshell/", "var/openshell/")
PUBLIC_PATHS = frozenset(
    {
        "artifacts/openshell/README.md",
        "artifacts/openshell/v0.6/baseline.json",
        "artifacts/openshell/v0.6/baseline.md",
        "artifacts/openshell/v0.6/readiness.json",
        "artifacts/openshell/v0.6/readiness.md",
        "var/openshell/README.md",
    }
)
CLASSIFICATIONS = frozenset(
    {
        "baseline",
        "public_document",
        "readiness",
        "sanitized_evidence",
        "sanitized_log",
        "sanitized_manifest",
    }
)
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SANITIZED_SUFFIX_RE = re.compile(r"\.sanitized\.(?:json|md)\Z")
MAX_MANIFEST_BYTES = 512 * 1024
MAX_MANIFEST_ENTRIES = 1024


def _git(repo_root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout


def cached_paths(repo_root: Path) -> list[str]:
    raw = _git(repo_root, "ls-files", "--cached", "-z", "--", "artifacts/openshell", "var/openshell")
    return sorted(item.decode("utf-8") for item in raw.split(b"\0") if item)


def _index_mode(repo_root: Path, path: str) -> set[str]:
    lines = _git(repo_root, "ls-files", "-s", "--", path).decode("utf-8", errors="replace").splitlines()
    return {line.split(maxsplit=1)[0] for line in lines if line.strip()}


def _index_blob(repo_root: Path, path: str) -> bytes:
    return _git(repo_root, "show", f":{path}")


def _normalized_manifest_path(value: Any) -> str | None:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        return None
    pure = PurePosixPath(value)
    if pure.is_absolute() or value != pure.as_posix() or any(part in {"", ".", ".."} for part in pure.parts):
        return None
    return value


def expected_classification(path: str) -> str | None:
    if path in {
        "artifacts/openshell/v0.6/baseline.json",
        "artifacts/openshell/v0.6/baseline.md",
    }:
        return "baseline"
    if path in {
        "artifacts/openshell/v0.6/readiness.json",
        "artifacts/openshell/v0.6/readiness.md",
    }:
        return "readiness"
    if path in {"artifacts/openshell/README.md", "var/openshell/README.md"}:
        return "public_document"
    pure = PurePosixPath(path)
    if (
        len(pure.parts) == 4
        and pure.parts[:3] == ("var", "openshell", "manifests")
        and SANITIZED_SUFFIX_RE.search(pure.name)
    ):
        return "sanitized_manifest"
    if path.startswith("artifacts/openshell/") and SANITIZED_SUFFIX_RE.search(path):
        is_log = pure.name.startswith("logs.sanitized.")
        return "sanitized_log" if is_log else "sanitized_evidence"
    return None


def _manifest_entries(content: bytes) -> tuple[dict[str, dict[str, Any]], list[Finding]]:
    findings = scan_content(MANIFEST_PATH, content, max_file_bytes=MAX_MANIFEST_BYTES)
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}, findings
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "artifacts"}:
        findings.append(Finding(MANIFEST_PATH, "tracked_manifest_fields_invalid"))
        return {}, findings
    if payload.get("schema_version") != MANIFEST_SCHEMA:
        findings.append(Finding(MANIFEST_PATH, "tracked_manifest_schema_invalid"))
    raw_entries = payload.get("artifacts")
    if not isinstance(raw_entries, list) or len(raw_entries) > MAX_MANIFEST_ENTRIES:
        findings.append(Finding(MANIFEST_PATH, "tracked_manifest_artifacts_invalid"))
        return {}, findings

    entries: dict[str, dict[str, Any]] = {}
    for index, raw_entry in enumerate(raw_entries):
        detail = f"artifacts[{index}]"
        if not isinstance(raw_entry, dict) or set(raw_entry) != {
            "classification",
            "path",
            "sha256",
            "size_bytes",
        }:
            findings.append(Finding(MANIFEST_PATH, "tracked_manifest_entry_fields_invalid", detail=detail))
            continue
        path = _normalized_manifest_path(raw_entry.get("path"))
        if path is None or path == MANIFEST_PATH:
            findings.append(Finding(MANIFEST_PATH, "tracked_manifest_path_invalid", detail=detail))
            continue
        classification = raw_entry.get("classification")
        expected = expected_classification(path)
        if classification not in CLASSIFICATIONS or expected is None or classification != expected:
            findings.append(Finding(MANIFEST_PATH, "tracked_manifest_classification_invalid", detail=detail))
            continue
        digest = raw_entry.get("sha256")
        size_bytes = raw_entry.get("size_bytes")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            findings.append(Finding(MANIFEST_PATH, "tracked_manifest_digest_invalid", detail=detail))
            continue
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or not 0 <= size_bytes <= 8 * 1024 * 1024:
            findings.append(Finding(MANIFEST_PATH, "tracked_manifest_size_invalid", detail=detail))
            continue
        if path in entries:
            findings.append(Finding(MANIFEST_PATH, "tracked_manifest_duplicate_path", detail=path))
            continue
        entries[path] = raw_entry
    if list(entries) != sorted(entries):
        findings.append(Finding(MANIFEST_PATH, "tracked_manifest_not_sorted"))
    return entries, findings


def _sanitized_log_pair_findings(
    entries: Mapping[str, Mapping[str, Any]],
    blobs: Mapping[str, bytes],
) -> list[Finding]:
    findings: list[Finding] = []
    pair_paths = {
        path
        for path in entries
        if PurePosixPath(path).name in {"logs.sanitized.json", "logs.sanitized.md"}
    }
    for path in sorted(pair_paths):
        pure = PurePosixPath(path)
        json_path = str(pure.with_name("logs.sanitized.json"))
        markdown_path = str(pure.with_name("logs.sanitized.md"))
        if json_path not in entries or markdown_path not in entries:
            findings.append(Finding(path, "sanitized_log_pair_missing"))
            continue
        if path != json_path or json_path not in blobs or markdown_path not in blobs:
            continue
        try:
            sanitized_log_contract.validate_pair(blobs[json_path], blobs[markdown_path])
        except sanitized_log_contract.SanitizedLogContractError as exc:
            code = "sanitized_log_markdown_mismatch" if str(exc) == "markdown_mismatch" else "sanitized_log_contract_invalid"
            findings.append(Finding(json_path, code, detail=str(exc)))
    return findings


def scan_tracked_state(
    repo_root: Path = REPO_ROOT,
    *,
    max_file_bytes: int = 8 * 1024 * 1024,
    require_allowlist: bool = False,
) -> list[Finding]:
    findings: list[Finding] = []
    tracked_paths = cached_paths(repo_root)
    tracked = {path for path in tracked_paths if path.startswith(OPEN_SHELL_PREFIXES)}
    if MANIFEST_PATH not in tracked:
        findings.append(Finding(MANIFEST_PATH, "tracked_manifest_not_tracked"))
        return findings
    if _index_mode(repo_root, MANIFEST_PATH) != {"100644"}:
        return [Finding(MANIFEST_PATH, "tracked_nonregular_mode")]
    try:
        manifest_content = _index_blob(repo_root, MANIFEST_PATH)
    except subprocess.CalledProcessError:
        return [Finding(MANIFEST_PATH, "index_blob_unreadable")]
    entries, manifest_findings = _manifest_entries(manifest_content)
    findings.extend(manifest_findings)
    if require_allowlist and not entries:
        findings.append(Finding(MANIFEST_PATH, "tracked_manifest_empty"))

    expected_paths = set(entries) | {MANIFEST_PATH}
    for path in sorted(tracked - expected_paths):
        findings.append(Finding(path, "tracked_path_not_manifested"))
    for path in sorted(set(entries) - tracked):
        findings.append(Finding(path, "manifested_path_not_tracked"))

    blobs: dict[str, bytes] = {}
    for path, entry in sorted(entries.items()):
        if path not in tracked:
            continue
        if _index_mode(repo_root, path) != {"100644"}:
            findings.append(Finding(path, "tracked_nonregular_mode"))
            continue
        try:
            content = _index_blob(repo_root, path)
        except subprocess.CalledProcessError:
            findings.append(Finding(path, "index_blob_unreadable"))
            continue
        blobs[path] = content
        if len(content) != entry["size_bytes"]:
            findings.append(Finding(path, "tracked_size_mismatch"))
        if hashlib.sha256(content).hexdigest() != entry["sha256"]:
            findings.append(Finding(path, "tracked_digest_mismatch"))
        findings.extend(scan_content(path, content, max_file_bytes=max_file_bytes))
    findings.extend(_sanitized_log_pair_findings(entries, blobs))
    return sorted(set(findings), key=lambda item: (item.path, item.line or 0, item.code, item.detail or ""))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--max-file-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument(
        "--require-allowlist",
        action="store_true",
        help="require a non-empty, internally complete tracked artifact manifest",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_file_bytes <= 0:
        _parser().error("--max-file-bytes must be positive")
    findings = scan_tracked_state(
        args.repo_root.resolve(),
        max_file_bytes=args.max_file_bytes,
        require_allowlist=args.require_allowlist,
    )
    result = {"ok": not findings, "finding_count": len(findings), "findings": [asdict(item) for item in findings]}
    if args.as_json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    elif findings:
        for finding in findings:
            location = f":{finding.line}" if finding.line else ""
            print(f"{finding.path}{location}: {finding.code}")
    else:
        print("tracked OpenShell state scan: PASS")
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
