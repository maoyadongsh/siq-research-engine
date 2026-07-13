#!/usr/bin/env python3
"""Write a redacted provenance and checksum manifest for release artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "siq_release_artifact_manifest_v1"
FAILURE_STATUSES = {"blocked", "error", "failed", "failure", "fail"}
LOCAL_PATH_RE = re.compile(r"(?:/(?:home|Users|tmp)/|[A-Za-z]:\\+Users\\+)")
SENSITIVE_KEY_RE = re.compile(
    r"(?:^|_)(?:api_?key|authorization|cookie|credential|dsn|password|passwd|secret|token)(?:$|_)",
    re.IGNORECASE,
)
REDACTED_VALUES = {
    "",
    "***",
    "<redacted>",
    "[redacted]",
    "configured",
    "invalid",
    "missing",
    "not_configured",
    "placeholder",
    "redacted",
    "unset",
}
REPO_ROOT = Path(__file__).resolve().parents[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git(*args: str, cwd: Path = REPO_ROOT) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(cwd), *args], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _git_bytes(*args: str, cwd: Path = REPO_ROOT) -> bytes | None:
    try:
        return subprocess.check_output(["git", "-C", str(cwd), *args], stderr=subprocess.DEVNULL)
    except (OSError, subprocess.CalledProcessError):
        return None


def _source_state(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    """Return reproducible source provenance without persisting paths or diff content."""
    head = _git("rev-parse", "HEAD", cwd=repo_root)
    tree = _git("rev-parse", "HEAD^{tree}", cwd=repo_root)
    status = _git_bytes("status", "--porcelain=v1", "--untracked-files=all", cwd=repo_root)
    diff = _git_bytes("diff", "--binary", "HEAD", "--", cwd=repo_root)
    untracked = _git_bytes("ls-files", "--others", "--exclude-standard", "-z", cwd=repo_root)
    if head == "unknown" or tree == "unknown" or status is None or diff is None or untracked is None:
        return {"available": False, "head_commit": head, "head_tree": tree, "dirty": None}

    status_lines = status.splitlines()
    staged_count = sum(1 for line in status_lines if line[:1] not in {b" ", b"?"})
    unstaged_count = sum(1 for line in status_lines if line[1:2] not in {b" ", b"?"})
    untracked_paths = [item for item in untracked.split(b"\0") if item]
    untracked_digest = hashlib.sha256()
    for raw_path in sorted(untracked_paths):
        path = repo_root / os.fsdecode(raw_path)
        untracked_digest.update(raw_path)
        untracked_digest.update(b"\0")
        try:
            if path.is_symlink():
                untracked_digest.update(os.fsencode(os.readlink(path)))
            elif path.is_file():
                untracked_digest.update(_sha256(path).encode("ascii"))
            else:
                untracked_digest.update(b"missing-or-special")
        except OSError:
            untracked_digest.update(b"unreadable")
        untracked_digest.update(b"\0")

    diff_sha256 = hashlib.sha256(diff).hexdigest()
    untracked_sha256 = untracked_digest.hexdigest()
    fingerprint = hashlib.sha256()
    for value in (head, tree, hashlib.sha256(status).hexdigest(), diff_sha256, untracked_sha256):
        fingerprint.update(value.encode("ascii"))
        fingerprint.update(b"\0")
    return {
        "available": True,
        "head_commit": head,
        "head_tree": tree,
        "dirty": bool(status_lines),
        "changed_entry_count": len(status_lines),
        "staged_entry_count": staged_count,
        "unstaged_entry_count": unstaged_count,
        "untracked_entry_count": len(untracked_paths),
        "tracked_diff_sha256": diff_sha256,
        "untracked_content_sha256": untracked_sha256,
        "worktree_fingerprint_sha256": fingerprint.hexdigest(),
    }


def _artifact_failed(payload: Any) -> bool:
    """Recognize the release report failure contracts without parsing row data."""
    if not isinstance(payload, dict):
        return False
    for key in ("passed", "gate_passed", "acceptance_passed", "p0_gate_passed"):
        if payload.get(key) is False:
            return True
    for key in ("status", "result", "gate_status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip().lower() in FAILURE_STATUSES:
            return True
    summary = payload.get("summary")
    if isinstance(summary, dict):
        for key in ("passed", "gate_passed", "acceptance_passed", "p0_gate_passed"):
            if summary.get(key) is False:
                return True
    return False


def _payload_contains_credential(payload: Any, *, parent_key: str = "") -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            if SENSITIVE_KEY_RE.search(key_text):
                if isinstance(value, str) and value.strip().lower() not in REDACTED_VALUES:
                    return True
                if value is not None and not isinstance(value, (str, bool)):
                    return True
            if _payload_contains_credential(value, parent_key=key_text):
                return True
    elif isinstance(payload, list):
        return any(_payload_contains_credential(item, parent_key=parent_key) for item in payload)
    return False


def _safe_required_names(required_artifacts: Iterable[str]) -> list[str]:
    names = sorted(set(required_artifacts))
    invalid = [name for name in names if not name or Path(name).name != name or name in {".", ".."}]
    if invalid:
        raise ValueError("required artifact names must be plain file names")
    return names


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _included_display_path(path: Path, *, artifact_dir: Path, repo_root: Path) -> str | None:
    path = path.resolve()
    artifact_parent = artifact_dir.parent.resolve()
    repo_root = repo_root.resolve()
    if not (_is_within(path, artifact_parent) or (_is_within(artifact_dir, repo_root) and _is_within(path, repo_root))):
        return None
    return Path(os.path.relpath(path, artifact_dir)).as_posix()


def _artifact_record(path: Path, display_path: str) -> dict[str, Any]:
    return {"path": display_path, "size_bytes": path.stat().st_size, "sha256": _sha256(path)}


def _checksum_key(path: Path, *, repo_root: Path, fallback: str) -> str:
    """Use the evidence-contract path form when an artifact belongs to the repo."""
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return fallback


def build_manifest(
    artifact_dir: Path,
    *,
    task_id: str,
    environment_profile: str,
    output_name: str = "release-artifact-manifest.json",
    checksum_name: str = "checksums.sha256",
    required_artifacts: Iterable[str] = (),
    include_artifacts: Iterable[Path] = (),
    include_dirs: Iterable[Path] = (),
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    started_at = time.monotonic()
    artifact_dir = artifact_dir.resolve()
    required_names = _safe_required_names(required_artifacts)
    files: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for path in sorted(item for item in artifact_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(artifact_dir).as_posix()
        if relative in {output_name, checksum_name}:
            continue
        resolved = path.resolve()
        seen_paths.add(resolved)
        files.append(_artifact_record(resolved, relative))

    missing_includes: list[str] = []
    include_policy_violations: list[dict[str, str]] = []
    requested_files: list[Path] = [Path(path) for path in include_artifacts]
    for requested_dir in include_dirs:
        directory = Path(requested_dir).resolve()
        display = _included_display_path(directory, artifact_dir=artifact_dir, repo_root=repo_root)
        if display is None:
            include_policy_violations.append({"path": directory.name, "code": "include_outside_allowed_roots"})
            continue
        if not directory.is_dir():
            missing_includes.append(display)
            continue
        directory_files = sorted(item for item in directory.rglob("*") if item.is_file())
        if not directory_files:
            missing_includes.append(display)
        requested_files.extend(directory_files)
    for requested_path in requested_files:
        path = requested_path.resolve()
        display = _included_display_path(path, artifact_dir=artifact_dir, repo_root=repo_root)
        if display is None:
            include_policy_violations.append({"path": path.name, "code": "include_outside_allowed_roots"})
            continue
        if not path.is_file():
            missing_includes.append(display)
            continue
        if path in seen_paths:
            continue
        seen_paths.add(path)
        files.append(_artifact_record(path, display))
    files.sort(key=lambda item: item["path"])

    available_names = {Path(item["path"]).name for item in files}
    missing_required = [name for name in required_names if name not in available_names]
    failed_artifacts: list[str] = []
    policy_violations: list[dict[str, str]] = include_policy_violations
    for item in files:
        if not item["path"].endswith((".json", ".md")):
            continue
        path = (artifact_dir / item["path"]).resolve()
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if LOCAL_PATH_RE.search(text):
            policy_violations.append({"path": item["path"], "code": "local_absolute_path"})
        if not item["path"].endswith(".json"):
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            continue
        if _payload_contains_credential(payload):
            policy_violations.append({"path": item["path"], "code": "credential_value"})
        if _artifact_failed(payload):
            failed_artifacts.append(item["path"])
    source_state = _source_state(repo_root)
    if not source_state["available"]:
        policy_violations.append({"path": "source_state", "code": "source_state_unavailable"})
    failures: list[dict[str, Any]] = []
    if failed_artifacts:
        failures.append({"code": "failed_artifacts", "count": len(failed_artifacts)})
    if policy_violations:
        failures.append({"code": "policy_violations", "count": len(policy_violations)})
    if missing_required:
        failures.append({"code": "missing_required_artifacts", "count": len(missing_required)})
    if missing_includes:
        failures.append({"code": "missing_included_artifacts", "count": len(set(missing_includes))})
    result = (
        "fail"
        if failed_artifacts or policy_violations or missing_required or missing_includes
        else "pass"
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now(),
        "base_commit": source_state["head_commit"],
        "worktree_dirty": source_state["dirty"],
        "worktree_summary": {
            key: source_state.get(key)
            for key in (
                "available",
                "changed_entry_count",
                "staged_entry_count",
                "unstaged_entry_count",
                "untracked_entry_count",
                "worktree_fingerprint_sha256",
            )
        },
        "source_state": source_state,
        "task_id": task_id,
        "environment_profile": environment_profile,
        "command": (
            "python scripts/maintenance/write_release_artifact_manifest.py "
            "--artifact-dir <artifact-dir> --required-artifact <configured-names> "
            "--include-artifact <configured-paths>"
        ),
        "result": result,
        "duration_seconds": round(time.monotonic() - started_at, 6),
        "failures": failures,
        "artifact_checksums": {
            _checksum_key(
                (artifact_dir / item["path"]).resolve(),
                repo_root=repo_root,
                fallback=item["path"],
            ): item["sha256"]
            for item in files
        },
        "required_artifacts": required_names,
        "missing_required_artifacts": missing_required,
        "missing_included_artifacts": sorted(set(missing_includes)),
        "failed_artifacts": failed_artifacts,
        "policy_violations": policy_violations,
        "artifact_count": len(files),
        "artifacts": files,
    }
    return manifest


def write_outputs(manifest: dict[str, Any], artifact_dir: Path, *, output_name: str, checksum_name: str) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    output_path = artifact_dir / output_name
    output_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    checksum_lines = [f"{item['sha256']}  {item['path']}" for item in manifest["artifacts"]]
    (artifact_dir / checksum_name).write_text("\n".join(checksum_lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--task-id", default="T12")
    parser.add_argument("--environment-profile", default="local-production-equivalent")
    parser.add_argument("--output-name", default="release-artifact-manifest.json")
    parser.add_argument("--checksum-name", default="checksums.sha256")
    parser.add_argument("--required-artifact", action="append", default=[])
    parser.add_argument("--include-artifact", type=Path, action="append", default=[])
    parser.add_argument("--include-dir", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    try:
        manifest = build_manifest(
            args.artifact_dir,
            task_id=args.task_id,
            environment_profile=args.environment_profile,
            output_name=args.output_name,
            checksum_name=args.checksum_name,
            required_artifacts=args.required_artifact,
            include_artifacts=args.include_artifact,
            include_dirs=args.include_dir,
        )
    except ValueError as exc:
        parser.error(str(exc))
    write_outputs(manifest, args.artifact_dir, output_name=args.output_name, checksum_name=args.checksum_name)
    print(json.dumps({"result": manifest["result"], "artifact_count": manifest["artifact_count"]}, ensure_ascii=False))
    return 0 if manifest["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
