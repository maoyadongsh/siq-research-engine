#!/usr/bin/env python3
"""Build the deterministic manifest for Git-trackable OpenShell artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts.openshell import check_sanitized_artifacts, sanitized_log_contract
except ModuleNotFoundError:  # direct execution from scripts/openshell
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check_sanitized_artifacts
    import sanitized_log_contract

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = Path("artifacts/openshell/tracked-artifacts.json")
SCHEMA_VERSION = "siq.openshell.tracked-artifacts.v1"
CLASSIFICATIONS = frozenset(
    {
        "public_document",
        "baseline",
        "readiness",
        "sanitized_manifest",
        "sanitized_evidence",
        "sanitized_log",
    }
)
SANITIZED_SUFFIX_RE = re.compile(r"\.sanitized\.(?:json|md)\Z")
ENTRY_KEYS = frozenset({"path", "classification", "sha256", "size_bytes"})
MAX_MANIFEST_BYTES = 512 * 1024
MAX_MANIFEST_ENTRIES = 1024


class TrackedArtifactManifestError(ValueError):
    """A manifest input or output failed a fail-closed validation."""


def _project_root(path: Path) -> Path:
    try:
        root = path.expanduser().resolve(strict=True)
    except OSError as exc:
        raise TrackedArtifactManifestError("project_root_invalid") from exc
    try:
        info = root.stat()
    except OSError as exc:
        raise TrackedArtifactManifestError("project_root_invalid") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise TrackedArtifactManifestError("project_root_invalid")
    return root


def _lexical_path(root: Path, requested: Path, *, code: str) -> tuple[Path, Path]:
    raw = os.fspath(requested)
    if not raw or "\x00" in raw or "\\" in raw:
        raise TrackedArtifactManifestError(code)
    candidate = requested.expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = Path(os.path.abspath(candidate))
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise TrackedArtifactManifestError(code) from exc
    if not relative.parts:
        raise TrackedArtifactManifestError(code)
    return candidate, relative


def _reject_symlink_components(root: Path, relative: Path, *, allow_missing_leaf: bool = False) -> None:
    current = root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            info = current.lstat()
        except FileNotFoundError:
            if allow_missing_leaf and index == len(relative.parts) - 1:
                return
            raise TrackedArtifactManifestError("path_component_missing") from None
        except OSError as exc:
            raise TrackedArtifactManifestError("path_component_unreadable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise TrackedArtifactManifestError("symlink_not_allowed")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise TrackedArtifactManifestError("path_parent_not_directory")


def _expected_classification(relative_path: str) -> str | None:
    if relative_path in {
        "artifacts/openshell/v0.6/baseline.json",
        "artifacts/openshell/v0.6/baseline.md",
    }:
        return "baseline"
    if relative_path in {
        "artifacts/openshell/v0.6/readiness.json",
        "artifacts/openshell/v0.6/readiness.md",
    }:
        return "readiness"
    if relative_path in {"artifacts/openshell/README.md", "var/openshell/README.md"}:
        return "public_document"
    pure = PurePosixPath(relative_path)
    if (
        len(pure.parts) == 4
        and pure.parts[:3] == ("var", "openshell", "manifests")
        and SANITIZED_SUFFIX_RE.search(pure.name)
    ):
        return "sanitized_manifest"
    if relative_path.startswith("artifacts/openshell/") and SANITIZED_SUFFIX_RE.search(relative_path):
        is_log = pure.name.startswith("logs.sanitized.")
        return "sanitized_log" if is_log else "sanitized_evidence"
    return None


def _validate_classification(relative_path: str, classification: str) -> None:
    if classification not in CLASSIFICATIONS:
        raise TrackedArtifactManifestError("artifact_classification_invalid")
    expected = _expected_classification(relative_path)
    if expected is None:
        raise TrackedArtifactManifestError("artifact_path_not_trackable")
    if classification != expected:
        raise TrackedArtifactManifestError("artifact_classification_mismatch")


def _hash_regular_file(path: Path) -> tuple[str, int]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise TrackedArtifactManifestError("artifact_open_failed") from exc
    digest = hashlib.sha256()
    size = 0
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise TrackedArtifactManifestError("artifact_not_regular_file")
        if info.st_nlink != 1:
            raise TrackedArtifactManifestError("artifact_hardlink_not_allowed")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size) != (info.st_dev, info.st_ino, size):
            raise TrackedArtifactManifestError("artifact_changed_during_read")
        try:
            current = path.lstat()
        except OSError as exc:
            raise TrackedArtifactManifestError("artifact_changed_during_read") from exc
        if (current.st_dev, current.st_ino, current.st_size, current.st_nlink) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_nlink,
        ):
            raise TrackedArtifactManifestError("artifact_changed_during_read")
    finally:
        os.close(descriptor)
    return digest.hexdigest(), size


def _artifact_entry(
    root: Path,
    output: Path,
    classification: str,
    requested: Path,
) -> tuple[dict[str, Any], Path]:
    candidate, relative = _lexical_path(root, requested, code="artifact_outside_project")
    _reject_symlink_components(root, relative)
    try:
        initial = candidate.lstat()
    except OSError as exc:
        raise TrackedArtifactManifestError("artifact_missing") from exc
    if not stat.S_ISREG(initial.st_mode):
        raise TrackedArtifactManifestError("artifact_not_regular_file")
    if initial.st_nlink != 1:
        raise TrackedArtifactManifestError("artifact_hardlink_not_allowed")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise TrackedArtifactManifestError("artifact_missing") from exc
    if resolved != candidate:
        raise TrackedArtifactManifestError("symlink_not_allowed")
    if resolved == output:
        raise TrackedArtifactManifestError("manifest_cannot_include_itself")
    relative_path = relative.as_posix()
    _validate_classification(relative_path, classification)
    digest, size = _hash_regular_file(resolved)
    return (
        {
            "path": relative_path,
            "classification": classification,
            "sha256": digest,
            "size_bytes": size,
        },
        resolved,
    )


def _parse_artifact_spec(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise TrackedArtifactManifestError("artifact_spec_invalid")
    classification, raw_path = value.split("=", 1)
    if not classification or not raw_path:
        raise TrackedArtifactManifestError("artifact_spec_invalid")
    return classification, Path(raw_path)


def _load_refresh_specs(path: Path) -> list[tuple[str, Path]]:
    _hash_regular_file(path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise TrackedArtifactManifestError("refresh_manifest_unreadable") from exc
    if len(content) > MAX_MANIFEST_BYTES:
        raise TrackedArtifactManifestError("refresh_manifest_too_large")
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TrackedArtifactManifestError("refresh_manifest_invalid") from exc
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "artifacts"}:
        raise TrackedArtifactManifestError("refresh_manifest_fields_invalid")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise TrackedArtifactManifestError("refresh_manifest_schema_invalid")
    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts or len(artifacts) > MAX_MANIFEST_ENTRIES:
        raise TrackedArtifactManifestError("refresh_manifest_artifacts_invalid")
    specs: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for entry in artifacts:
        if not isinstance(entry, dict) or set(entry) != ENTRY_KEYS:
            raise TrackedArtifactManifestError("refresh_manifest_entry_fields_invalid")
        relative_path = entry.get("path")
        classification = entry.get("classification")
        digest = entry.get("sha256")
        size = entry.get("size_bytes")
        if not isinstance(relative_path, str) or not relative_path or "\\" in relative_path or "\x00" in relative_path:
            raise TrackedArtifactManifestError("refresh_manifest_path_invalid")
        pure = PurePosixPath(relative_path)
        if (
            pure.is_absolute()
            or relative_path != pure.as_posix()
            or any(part in {"", ".", ".."} for part in pure.parts)
        ):
            raise TrackedArtifactManifestError("refresh_manifest_path_invalid")
        if relative_path in seen:
            raise TrackedArtifactManifestError("artifact_duplicate")
        seen.add(relative_path)
        if not isinstance(classification, str):
            raise TrackedArtifactManifestError("artifact_classification_invalid")
        _validate_classification(relative_path, classification)
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise TrackedArtifactManifestError("refresh_manifest_digest_invalid")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise TrackedArtifactManifestError("refresh_manifest_size_invalid")
        specs.append((classification, Path(relative_path)))
    return specs


def _output_path(root: Path, requested: Path) -> tuple[Path, Path]:
    output, relative = _lexical_path(root, requested, code="manifest_output_outside_project")
    parent_relative = relative.parent
    current = root
    for part in parent_relative.parts:
        current /= part
        try:
            current.mkdir(mode=0o700)
        except FileExistsError:
            pass
        try:
            info = current.lstat()
        except OSError as exc:
            raise TrackedArtifactManifestError("manifest_output_parent_invalid") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise TrackedArtifactManifestError("manifest_output_parent_invalid")
    try:
        info = output.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise TrackedArtifactManifestError("manifest_output_invalid") from exc
    else:
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise TrackedArtifactManifestError("manifest_output_invalid")
    return output, relative


def build_manifest(
    *,
    project_root: Path,
    output: Path,
    artifacts: Iterable[tuple[str, Path]],
) -> tuple[dict[str, Any], list[Path]]:
    root = _project_root(project_root)
    output_path, _ = _output_path(root, output)
    requested = list(artifacts)
    if not requested or len(requested) > MAX_MANIFEST_ENTRIES:
        raise TrackedArtifactManifestError("artifact_count_invalid")
    entries: list[dict[str, Any]] = []
    paths: list[Path] = []
    seen: set[str] = set()
    for classification, path in requested:
        entry, resolved = _artifact_entry(root, output_path, classification, path)
        if entry["path"] in seen:
            raise TrackedArtifactManifestError("artifact_duplicate")
        seen.add(entry["path"])
        entries.append(entry)
        paths.append(resolved)
    ordered = sorted(zip(entries, paths, strict=True), key=lambda item: item[0]["path"])
    payload = {"schema_version": SCHEMA_VERSION, "artifacts": [item[0] for item in ordered]}
    return payload, [item[1] for item in ordered]


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")


def _validate_sanitized_log_pairs(payload: Mapping[str, Any], paths: Sequence[Path]) -> None:
    entries = payload["artifacts"]
    by_relative = {str(entry["path"]): path for entry, path in zip(entries, paths, strict=True)}
    pair_paths = {
        relative
        for relative in by_relative
        if PurePosixPath(relative).name in {"logs.sanitized.json", "logs.sanitized.md"}
    }
    for relative in sorted(pair_paths):
        pure = PurePosixPath(relative)
        json_relative = str(pure.with_name("logs.sanitized.json"))
        markdown_relative = str(pure.with_name("logs.sanitized.md"))
        if json_relative not in by_relative or markdown_relative not in by_relative:
            raise TrackedArtifactManifestError("sanitized_log_pair_missing")
        if relative != json_relative:
            continue
        try:
            sanitized_log_contract.validate_pair(
                by_relative[json_relative].read_bytes(),
                by_relative[markdown_relative].read_bytes(),
            )
        except sanitized_log_contract.SanitizedLogContractError as exc:
            raise TrackedArtifactManifestError(f"sanitized_log_contract_invalid:{exc}") from exc


def _atomic_write(path: Path, content: bytes) -> None:
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
        directory_descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except OSError as exc:
        raise TrackedArtifactManifestError("manifest_write_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)


def write_manifest(
    *,
    project_root: Path,
    output: Path,
    artifacts: Sequence[tuple[str, Path]],
) -> dict[str, Any]:
    root = _project_root(project_root)
    output_path, _ = _output_path(root, output)
    payload, paths = build_manifest(project_root=root, output=output_path, artifacts=artifacts)
    findings = check_sanitized_artifacts.scan_paths(paths)
    if findings:
        codes = ",".join(sorted({finding.code for finding in findings}))
        raise TrackedArtifactManifestError(f"artifact_sanitization_failed:{codes}")
    _validate_sanitized_log_pairs(payload, paths)
    verified_payload, verified_paths = build_manifest(project_root=root, output=output_path, artifacts=artifacts)
    if payload != verified_payload or paths != verified_paths:
        raise TrackedArtifactManifestError("artifact_changed_during_scan")
    _atomic_write(output_path, _canonical_bytes(verified_payload))
    return verified_payload


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--artifact",
        action="append",
        metavar="CLASSIFICATION=PATH",
        help="explicit trackable artifact; repeat for every artifact",
    )
    source.add_argument("--refresh", action="store_true", help="recompute entries from the existing output manifest")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = _project_root(args.project_root)
        output, _ = _output_path(root, args.output)
        if args.refresh:
            if not output.exists():
                raise TrackedArtifactManifestError("refresh_manifest_missing")
            specs = _load_refresh_specs(output)
        else:
            specs = [_parse_artifact_spec(value) for value in args.artifact]
        payload = write_manifest(project_root=root, output=output, artifacts=specs)
        print(
            json.dumps(
                {
                    "ok": True,
                    "schema_version": SCHEMA_VERSION,
                    "artifact_count": len(payload["artifacts"]),
                },
                sort_keys=True,
            )
        )
        return 0
    except (OSError, TrackedArtifactManifestError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
