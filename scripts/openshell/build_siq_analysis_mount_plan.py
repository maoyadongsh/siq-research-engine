#!/usr/bin/env python3
"""Build the fixed Docker bind-mount plan for one siq_analysis sandbox task."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import quote

SCHEMA_VERSION = "siq.openshell.siq_analysis_mount_plan.v2"
SNAPSHOT_SCHEMA_VERSION = "siq.openshell.siq_analysis_runtime_snapshot.v3"
PROFILE = "siq_analysis"
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROJECT_ROOT = REPO_ROOT
WIKI_RELATIVE = Path("data/wiki")
COMPANY_ROOTS = {
    "cn": WIKI_RELATIVE / "companies",
    "eu": WIKI_RELATIVE / "eu/companies",
    "hk": WIKI_RELATIVE / "hk/companies",
    "jp": WIKI_RELATIVE / "jp/companies",
    "kr": WIKI_RELATIVE / "kr/companies",
    "us": WIKI_RELATIVE / "us/companies",
}
HERMES_HOME_RELATIVE = Path("data/hermes/home/profiles/siq_analysis")
SNAPSHOT_ROOT_RELATIVE = Path("var/openshell/siq-analysis/runtime-snapshots")
CONTEXT_ROOT_RELATIVE = Path("var/openshell/siq-analysis/contexts")
PLAN_ROOT_RELATIVE = Path("var/openshell/siq-analysis/mount-plans")
SNAPSHOT_MANIFEST = "snapshot-manifest.json"
SQLITE_DATABASES = ("state.db", "response_store.db")
SQLITE_SIDECARS = tuple(f"{database}{suffix}" for database in SQLITE_DATABASES for suffix in ("-wal", "-shm"))
RUNTIME_STATE_DIRECTORY = "runtime-state"
SANDBOX_RUNTIME_STATE_ROOT = Path("/sandbox/siq-analysis-runtime-state")
RUNTIME_DIRECTORIES = ("sessions", "checkpoints", "cron", "memories")
FRESH_SNAPSHOT_MODE = "fresh"
BUSINESS_MOUNT_COUNT = 3 + len(RUNTIME_DIRECTORIES)
SNAPSHOT_TOP_LEVEL = {
    "config.yaml",
    RUNTIME_STATE_DIRECTORY,
    *RUNTIME_DIRECTORIES,
    SNAPSHOT_MANIFEST,
}
SNAPSHOT_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
PLAN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.driver-config\.json")
SENSITIVE_NAME_RE = re.compile(
    r"(?:^|[._-])(?:auth|authorization|aws|cookie|credential|credentials|env|key|lock|password|pem|pid|secret|shm|ssh|tls|token|tokens|wal)(?:$|[._-])"
)
SENSITIVE_EXACT_NAMES = {"id_ed25519", "id_rsa"}
ANALYSIS_SENSITIVE_NAMES = {
    ".env",
    "auth.json",
    "auth.lock",
    "credentials.json",
    "secrets.json",
    "token.json",
    *SENSITIVE_EXACT_NAMES,
}
ANALYSIS_SENSITIVE_SUFFIXES = (".key", ".p12", ".pem", ".pfx")
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


class MountPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class CompiledMountPlan:
    plan: dict[str, Any]
    summary: dict[str, Any]
    content: bytes
    summary_content: bytes
    digest: str


def _absolute_normalized(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    if not expanded.is_absolute():
        raise MountPlanError(f"{label} must be absolute")
    if ".." in expanded.parts:
        raise MountPlanError(f"{label} must not contain '..'")
    return Path(os.path.normpath(os.fspath(expanded)))


def _assert_no_symlink_components(path: Path, *, label: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            break
        if stat.S_ISLNK(mode):
            raise MountPlanError(f"{label} uses a symlink: {current}")


def _require_directory(path: Path, *, label: str, writable: bool = False) -> os.stat_result:
    _assert_no_symlink_components(path, label=label)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise MountPlanError(f"{label} does not exist") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise MountPlanError(f"{label} must be a non-symlink directory")
    if writable and not os.access(path, os.W_OK | os.X_OK):
        raise MountPlanError(f"{label} must be writable by the current user")
    return info


def _require_regular_file(path: Path, *, label: str, writable: bool = False) -> os.stat_result:
    _assert_no_symlink_components(path, label=label)
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise MountPlanError(f"{label} does not exist") from exc
    if not stat.S_ISREG(info.st_mode):
        raise MountPlanError(f"{label} must be a regular, non-symlink file")
    if info.st_nlink != 1:
        raise MountPlanError(f"{label} must not be a hard link")
    if writable and not os.access(path, os.W_OK):
        raise MountPlanError(f"{label} must be writable by the current user")
    return info


def _mkdir_chain(root: Path, relative: Path) -> Path:
    current = root
    for part in relative.parts:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            current.mkdir(mode=0o700)
            continue
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise MountPlanError(f"managed output root component is unsafe: {current}")
    return current


def _sha256_file(path: Path) -> tuple[int, str]:
    expected = _require_regular_file(path, label="snapshot file")
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    digest = hashlib.sha256()
    byte_count = 0
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise MountPlanError(f"snapshot file changed while opening: {path.name}")
        while chunk := os.read(descriptor, 1024 * 1024):
            digest.update(chunk)
            byte_count += len(chunk)
        finished = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (finished.st_size, finished.st_mtime_ns, finished.st_ino) != (
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ino,
    ):
        raise MountPlanError(f"snapshot file changed while hashing: {path.name}")
    return byte_count, digest.hexdigest()


def _tree_digest(records: list[tuple[str, str, int, str]]) -> str:
    digest = hashlib.sha256()
    for relative, kind, byte_count, content_sha256 in sorted(records):
        digest.update(f"{kind}\0{relative}\0{byte_count}\0{content_sha256}\n".encode())
    return digest.hexdigest()


def _scan_regular_tree(
    root: Path,
    *,
    label: str,
    reject_sensitive_names: bool,
    reject_analysis_secrets: bool = False,
) -> dict[str, Any]:
    _require_directory(root, label=label, writable=True)
    records: list[tuple[str, str, int, str]] = [(root.name, "directory", 0, "")]
    stack = [root]
    while stack:
        current = stack.pop()
        for child in sorted(current.iterdir(), key=lambda item: item.name, reverse=True):
            relative = child.relative_to(root.parent).as_posix()
            info = child.lstat()
            if stat.S_ISLNK(info.st_mode):
                raise MountPlanError(f"{label} contains a symlink: {relative}")
            lowered_name = child.name.lower()
            if reject_sensitive_names and (
                lowered_name in SENSITIVE_EXACT_NAMES or SENSITIVE_NAME_RE.search(lowered_name)
            ):
                raise MountPlanError(f"{label} contains a forbidden runtime artifact")
            if reject_analysis_secrets and (
                lowered_name in ANALYSIS_SENSITIVE_NAMES
                or lowered_name.startswith(".env.")
                or lowered_name.endswith(ANALYSIS_SENSITIVE_SUFFIXES)
            ):
                raise MountPlanError(f"{label} contains a forbidden runtime artifact")
            if stat.S_ISDIR(info.st_mode):
                records.append((relative, "directory", 0, ""))
                stack.append(child)
                continue
            if not stat.S_ISREG(info.st_mode):
                raise MountPlanError(f"{label} contains a non-regular entry: {relative}")
            byte_count, content_sha256 = _sha256_file(child)
            records.append((relative, "file", byte_count, content_sha256))
    files = [record for record in records if record[1] == "file"]
    directories = [record for record in records if record[1] == "directory"]
    return {
        "file_count": len(files),
        "directory_count": len(directories),
        "byte_count": sum(record[2] for record in files),
        "tree_sha256": _tree_digest(records),
    }


def _load_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    _require_regular_file(path, label=label)
    content = path.read_bytes()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as exc:
        raise MountPlanError(f"{label} is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise MountPlanError(f"{label} must contain an object")
    return payload, content


def _validate_database(path: Path, expected: Mapping[str, Any]) -> None:
    byte_count, content_sha256 = _sha256_file(path)
    if expected.get("name") != path.name:
        raise MountPlanError("snapshot database manifest order or name is invalid")
    if expected.get("byte_count") != byte_count or expected.get("sha256") != content_sha256:
        raise MountPlanError(f"snapshot database digest is stale: {path.name}")
    if expected.get("backup_method") != "python_sqlite3_connection_backup":
        raise MountPlanError(f"snapshot database was not created with sqlite3 backup: {path.name}")
    if expected.get("integrity_check") != "ok" or expected.get("journal_mode") != "delete":
        raise MountPlanError(f"snapshot database manifest is not crash-safe: {path.name}")
    uri = f"file:{quote(path.as_posix(), safe='/')}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=10.0) as connection:
            connection.execute("PRAGMA query_only = ON")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise MountPlanError(f"snapshot database integrity check failed: {path.name}") from exc
    if not integrity or integrity[0] != "ok":
        raise MountPlanError(f"snapshot database integrity check failed: {path.name}")


def _validate_snapshot(project_root: Path, snapshot: Path) -> tuple[dict[str, Any], str]:
    expected_root = project_root / SNAPSHOT_ROOT_RELATIVE
    if snapshot.parent != expected_root or not SNAPSHOT_NAME_RE.fullmatch(snapshot.name):
        raise MountPlanError("snapshot must be a named child of the managed runtime snapshot root")
    info = _require_directory(snapshot, label="runtime snapshot", writable=True)
    if info.st_mode & 0o077:
        raise MountPlanError("runtime snapshot must not grant group or world permissions")

    top_level = {entry.name for entry in snapshot.iterdir()}
    if top_level != SNAPSHOT_TOP_LEVEL:
        raise MountPlanError("runtime snapshot top-level entries differ from the fixed contract")
    manifest, manifest_content = _load_json(snapshot / SNAPSHOT_MANIFEST, label="snapshot manifest")
    if manifest.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise MountPlanError("snapshot manifest schema is unsupported")
    if manifest.get("profile") != PROFILE or manifest.get("snapshot_kind") != "isolated_runtime":
        raise MountPlanError("snapshot manifest profile or kind is invalid")

    snapshot_mode = manifest.get("snapshot_mode")
    if snapshot_mode not in (None, FRESH_SNAPSHOT_MODE):
        raise MountPlanError("snapshot mode is unsupported")
    fresh = snapshot_mode == FRESH_SNAPSHOT_MODE
    if fresh and (
        manifest.get("source_scope") != "current_project_siq_analysis_config_only"
        or manifest.get("host_runtime_records_copied") is not False
    ):
        raise MountPlanError("fresh snapshot host runtime isolation is incomplete")

    copy_policy = manifest.get("copy_policy")
    expected_copy_policy = {
        "allowlist_only": True,
        "config_files": ["config.yaml"],
        "runtime_state_directory": RUNTIME_STATE_DIRECTORY,
        "sqlite_databases": [] if fresh else list(SQLITE_DATABASES),
        "sqlite_sidecars": [] if fresh else list(SQLITE_SIDECARS),
        "runtime_directories": list(RUNTIME_DIRECTORIES),
    }
    if not isinstance(copy_policy, dict) or copy_policy != expected_copy_policy:
        raise MountPlanError("snapshot copy policy differs from the fixed contract")
    safeguards = manifest.get("safeguards")
    required_safeguards = {
        "source_opened_read_only": True,
        "sqlite_backup_api": not fresh,
        "credentials_copied": False,
        "tls_material_copied": False,
        "host_process_state_copied": False,
        "sqlite_sidecars_copied": False,
        "sqlite_sidecars_materialized_empty": not fresh,
        "symlinks_allowed": False,
    }
    if fresh:
        required_safeguards["host_runtime_records_copied"] = False
    if not isinstance(safeguards, dict) or any(
        safeguards.get(key) != value for key, value in required_safeguards.items()
    ):
        raise MountPlanError("snapshot safeguards are incomplete")

    inventory = manifest.get("inventory")
    if not isinstance(inventory, dict):
        raise MountPlanError("snapshot inventory is missing")
    config = inventory.get("config")
    config_bytes, config_sha256 = _sha256_file(snapshot / "config.yaml")
    if (
        not isinstance(config, dict)
        or config.get("byte_count") != config_bytes
        or config.get("tree_sha256") != config_sha256
    ):
        raise MountPlanError("snapshot config digest is stale")

    runtime_state = snapshot / RUNTIME_STATE_DIRECTORY
    runtime_state_info = _require_directory(runtime_state, label="snapshot runtime state", writable=True)
    if runtime_state_info.st_mode & 0o077:
        raise MountPlanError("snapshot runtime state must not grant group or world permissions")
    expected_runtime_state_entries = set() if fresh else {*SQLITE_DATABASES, *SQLITE_SIDECARS}
    if {entry.name for entry in runtime_state.iterdir()} != expected_runtime_state_entries:
        raise MountPlanError("snapshot runtime state entries differ from the fixed contract")

    databases = inventory.get("databases")
    if fresh and databases != []:
        raise MountPlanError("fresh snapshot database inventory must be empty")
    if not fresh and (not isinstance(databases, list) or len(databases) != len(SQLITE_DATABASES)):
        raise MountPlanError("snapshot database inventory is incomplete")
    if not fresh:
        for name, expected in zip(SQLITE_DATABASES, databases, strict=True):
            if not isinstance(expected, dict):
                raise MountPlanError("snapshot database inventory entry is invalid")
            database = snapshot / RUNTIME_STATE_DIRECTORY / name
            _require_regular_file(database, label="snapshot database", writable=True)
            _validate_database(database, expected)

    sidecars = inventory.get("sqlite_sidecars")
    if fresh and sidecars != []:
        raise MountPlanError("fresh snapshot SQLite sidecar inventory must be empty")
    if not fresh and (not isinstance(sidecars, list) or len(sidecars) != len(SQLITE_SIDECARS)):
        raise MountPlanError("snapshot SQLite sidecar inventory is incomplete")
    if not fresh:
        for name, expected in zip(SQLITE_SIDECARS, sidecars, strict=True):
            sidecar = snapshot / RUNTIME_STATE_DIRECTORY / name
            info = _require_regular_file(sidecar, label="snapshot SQLite sidecar", writable=True)
            if info.st_size != 0:
                raise MountPlanError("snapshot SQLite sidecars must be empty before sandbox start")
            if not isinstance(expected, dict) or expected != {
                "name": name,
                "byte_count": 0,
                "sha256": EMPTY_SHA256,
                "materialization": "empty_not_copied_from_host",
            }:
                raise MountPlanError("snapshot SQLite sidecar manifest is invalid")

    runtime_inventory = inventory.get("runtime_entries")
    if not isinstance(runtime_inventory, dict) or set(runtime_inventory) != set(RUNTIME_DIRECTORIES):
        raise MountPlanError("snapshot runtime directory inventory is incomplete")
    for name in RUNTIME_DIRECTORIES:
        expected = runtime_inventory[name]
        if not isinstance(expected, dict) or expected.get("present") is not True:
            raise MountPlanError(f"snapshot runtime directory manifest is invalid: {name}")
        observed = _scan_regular_tree(
            snapshot / name,
            label=f"snapshot runtime directory {name}",
            reject_sensitive_names=True,
        )
        if fresh and (
            expected.get("source_copied") is not False
            or expected.get("materialized_empty") is not True
            or observed["file_count"] != 0
            or observed["directory_count"] != 1
            or observed["byte_count"] != 0
        ):
            raise MountPlanError(f"fresh snapshot runtime directory is not empty: {name}")
        for field in ("file_count", "directory_count", "byte_count", "tree_sha256"):
            if expected.get(field) != observed[field]:
                raise MountPlanError(f"snapshot runtime directory digest is stale: {name}")
    if fresh and (
        inventory.get("skipped_forbidden_artifact_count") != 0 or inventory.get("total_file_bytes") != config_bytes
    ):
        raise MountPlanError("fresh snapshot inventory includes host runtime records")
    return manifest, hashlib.sha256(manifest_content).hexdigest()


def _validate_analysis_directory(project_root: Path, analysis_dir: Path) -> str:
    relative: Path | None = None
    for companies_relative in COMPANY_ROOTS.values():
        try:
            candidate = analysis_dir.relative_to(project_root / companies_relative)
        except ValueError:
            continue
        relative = candidate
        break
    if relative is None:
        raise MountPlanError("analysis directory must be under a supported market company root")
    if len(relative.parts) != 2 or relative.parts[1] != "analysis":
        raise MountPlanError("analysis directory must be one company's direct analysis child")
    company = relative.parts[0]
    if (
        company in {"", ".", ".."}
        or len(company) > 128
        or not company[0].isalnum()
        or any(not (character.isalnum() or character in "-_.()（）") for character in company)
    ):
        raise MountPlanError("company directory name is unsafe")
    _scan_regular_tree(
        analysis_dir,
        label="task analysis directory",
        reject_sensitive_names=False,
        reject_analysis_secrets=True,
    )
    return analysis_dir.relative_to(project_root).as_posix()


def _bind(source: Path, target: Path, *, read_only: bool) -> dict[str, Any]:
    return {
        "type": "bind",
        "source": source.as_posix(),
        "target": target.as_posix(),
        "read_only": read_only,
    }


def _expected_mounts(project_root: Path, snapshot: Path, analysis_dir: Path) -> list[dict[str, Any]]:
    wiki = project_root / WIKI_RELATIVE
    hermes_home = project_root / HERMES_HOME_RELATIVE
    mounts = [
        _bind(wiki, wiki, read_only=True),
        _bind(analysis_dir, analysis_dir, read_only=False),
        _bind(snapshot / RUNTIME_STATE_DIRECTORY, SANDBOX_RUNTIME_STATE_ROOT, read_only=False),
    ]
    for name in RUNTIME_DIRECTORIES:
        mounts.append(_bind(snapshot / name, hermes_home / name, read_only=False))
    return mounts


def _is_ancestor(parent: Path, child: Path) -> bool:
    return parent != child and parent in child.parents


def _contains_absolute_path(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(_contains_absolute_path(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_absolute_path(child) for child in value)
    return isinstance(value, str) and value.startswith("/")


def validate_mount_plan(
    plan: Mapping[str, Any],
    *,
    project_root: Path,
    snapshot: Path,
    analysis_dir: Path,
) -> None:
    docker = plan.get("docker")
    mounts = docker.get("mounts") if isinstance(docker, Mapping) else None
    expected = _expected_mounts(project_root, snapshot, analysis_dir)
    observed = list(mounts) if isinstance(mounts, list) else None
    if observed != expected:
        raise MountPlanError("mounts differ from the fixed siq_analysis contract")

    forbidden_sources = {
        project_root,
        project_root / HERMES_HOME_RELATIVE,
        project_root / SNAPSHOT_ROOT_RELATIVE,
        project_root / CONTEXT_ROOT_RELATIVE,
        analysis_dir.parent,
    }
    forbidden_targets = {project_root, project_root / HERMES_HOME_RELATIVE, analysis_dir.parent}
    targets: list[Path] = []
    for mount in expected:
        source = Path(mount["source"])
        target = Path(mount["target"])
        if source in forbidden_sources:
            raise MountPlanError("mount plan includes a forbidden parent source")
        if target in forbidden_targets:
            raise MountPlanError("mount plan includes a dangerous parent target")
        if source.name.lower() in {"auth.json", ".env", "gateway.pid", "gateway.lock"}:
            raise MountPlanError("mount plan includes a credential or host process artifact")
        if target in targets:
            raise MountPlanError("mount plan contains a duplicate target")
        targets.append(target)

    wiki_target = project_root / WIKI_RELATIVE
    for index, left in enumerate(targets):
        for right in targets[index + 1 :]:
            if not (_is_ancestor(left, right) or _is_ancestor(right, left)):
                continue
            parent, child = (left, right) if _is_ancestor(left, right) else (right, left)
            if parent != wiki_target or child != analysis_dir:
                raise MountPlanError("mount plan contains an unsafe overlapping target")


def compile_mount_plan(
    *,
    project_root: Path,
    snapshot: Path,
    analysis_dir: Path,
) -> CompiledMountPlan:
    project_root = _absolute_normalized(project_root, label="project root")
    snapshot = _absolute_normalized(snapshot, label="runtime snapshot")
    analysis_dir = _absolute_normalized(analysis_dir, label="analysis directory")
    _require_directory(project_root, label="project root")
    if project_root in {Path("/"), Path("/home"), Path("/tmp"), Path("/var"), Path.home()}:
        raise MountPlanError("project root is dangerous")
    _require_directory(project_root / WIKI_RELATIVE, label="data/wiki")
    analysis_relative = _validate_analysis_directory(project_root, analysis_dir)
    _, manifest_sha256 = _validate_snapshot(project_root, snapshot)

    mounts = _expected_mounts(project_root, snapshot, analysis_dir)
    if len(mounts) != BUSINESS_MOUNT_COUNT:
        raise MountPlanError("internal siq_analysis mount count mismatch")
    plan = {"docker": {"mounts": mounts}}
    validate_mount_plan(
        plan,
        project_root=project_root,
        snapshot=snapshot,
        analysis_dir=analysis_dir,
    )
    content = (json.dumps(plan, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = hashlib.sha256(content).hexdigest()
    summary = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "driver_config_sha256": digest,
        "snapshot_manifest_sha256": manifest_sha256,
        "analysis_relative_path": analysis_relative,
        "mount_count": len(mounts),
        "mount_classes": {
            "wiki_read_only": 1,
            "task_analysis_read_write": 1,
            "runtime_state_directory_read_write": 1,
            "runtime_directories_read_write": len(RUNTIME_DIRECTORIES),
        },
        "repository_root_mounted": False,
        "hermes_home_mounted": False,
        "context_mounted": False,
        "contains_absolute_host_paths": False,
    }
    summary_content = (json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    if _contains_absolute_path(summary) or project_root.as_posix().encode() in summary_content:
        raise MountPlanError("mount plan summary contains an absolute host path")
    return CompiledMountPlan(
        plan=plan,
        summary=summary,
        content=content,
        summary_content=summary_content,
        digest=digest,
    )


def _write_once(path: Path, content: bytes) -> None:
    if path.is_symlink():
        raise MountPlanError(f"refusing symlink output: {path.name}")
    if path.exists():
        if not path.is_file() or path.read_bytes() != content:
            raise MountPlanError(f"existing mount plan output conflicts: {path.name}")
        return
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
                raise MountPlanError(f"concurrent mount plan output conflicts: {path.name}") from exc
    finally:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)


def write_compiled_mount_plan(
    compiled: CompiledMountPlan,
    *,
    project_root: Path,
    output: Path | None = None,
) -> tuple[Path, Path]:
    project_root = _absolute_normalized(project_root, label="project root")
    _require_directory(project_root, label="project root")
    output_root = _mkdir_chain(project_root, PLAN_ROOT_RELATIVE)
    default_name = f"{compiled.digest}.driver-config.json"
    plan_path = _absolute_normalized(output or (output_root / default_name), label="mount plan output")
    if plan_path.parent != output_root or not PLAN_NAME_RE.fullmatch(plan_path.name):
        raise MountPlanError("mount plan output must be a driver-config JSON file in the managed output root")
    summary_path = output_root / plan_path.name.removesuffix(".driver-config.json")
    summary_path = summary_path.with_name(f"{summary_path.name}.summary.json")
    _assert_no_symlink_components(plan_path, label="mount plan output")
    _assert_no_symlink_components(summary_path, label="mount plan summary output")
    _write_once(plan_path, compiled.content)
    _write_once(summary_path, compiled.summary_content)
    return plan_path, summary_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--analysis-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        compiled = compile_mount_plan(
            project_root=args.project_root,
            snapshot=args.snapshot,
            analysis_dir=args.analysis_dir,
        )
        plan_path, summary_path = write_compiled_mount_plan(
            compiled,
            project_root=args.project_root,
            output=args.output,
        )
        print(
            json.dumps(
                {
                    "driver_config": plan_path.relative_to(args.project_root.resolve(strict=True)).as_posix(),
                    "summary": summary_path.relative_to(args.project_root.resolve(strict=True)).as_posix(),
                    "sha256": compiled.digest,
                    "mount_count": compiled.summary["mount_count"],
                },
                ensure_ascii=True,
                sort_keys=True,
            )
        )
        return 0
    except (OSError, MountPlanError) as exc:
        print(f"siq_analysis mount plan build failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
