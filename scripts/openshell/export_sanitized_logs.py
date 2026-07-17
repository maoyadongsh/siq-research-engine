#!/usr/bin/env python3
"""Publish aggregate OpenShell audit and message-free operational log metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

try:
    from scripts.openshell import aggregate_security_audit, check_sanitized_artifacts, sanitized_log_contract
except ModuleNotFoundError:  # direct execution from scripts/openshell
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.openshell import aggregate_security_audit, check_sanitized_artifacts, sanitized_log_contract


SCHEMA_VERSION = sanitized_log_contract.SCHEMA_VERSION
OUTPUT_JSON_NAME = "logs.sanitized.json"
OUTPUT_MARKDOWN_NAME = "logs.sanitized.md"
MAX_OPERATIONAL_LOG_BYTES = 64 * 1024 * 1024
MAX_OPERATIONAL_LOGS = 64
READ_CHUNK_BYTES = 1024 * 1024
COMPONENT_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
SEVERITY_ORDER = ("critical", "error", "warning", "info", "debug", "unclassified")
EXPLICIT_LEVEL_RE = re.compile(
    rb"^\s*(?:\d{4}-\d{2}-\d{2}T\S+\s+)?(?P<level>critical|fatal|panic|error|err|warning|warn|info|debug|trace)\b",
    re.IGNORECASE,
)
SEVERITY_PATTERNS = (
    ("critical", re.compile(rb"(?:^|[^a-z0-9])(?:critical|fatal|panic)(?:[^a-z0-9]|$)", re.IGNORECASE)),
    ("error", re.compile(rb"(?:^|[^a-z0-9])(?:error|err)(?:[^a-z0-9]|$)", re.IGNORECASE)),
    ("warning", re.compile(rb"(?:^|[^a-z0-9])(?:warning|warn)(?:[^a-z0-9]|$)", re.IGNORECASE)),
    ("info", re.compile(rb"(?:^|[^a-z0-9])info(?:[^a-z0-9]|$)", re.IGNORECASE)),
    ("debug", re.compile(rb"(?:^|[^a-z0-9])(?:debug|trace)(?:[^a-z0-9]|$)", re.IGNORECASE)),
)


class SanitizedLogExportError(RuntimeError):
    """Stable publication failure that never includes source content or paths."""


def _absolute(path: Path) -> Path:
    candidate = path.expanduser()
    return candidate if candidate.is_absolute() else Path.cwd() / candidate


def _assert_no_symlink_chain(path: Path, *, missing_code: str, symlink_code: str) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise SanitizedLogExportError(missing_code) from exc
        if stat.S_ISLNK(mode):
            raise SanitizedLogExportError(symlink_code)


def parse_operational_specs(values: Iterable[str]) -> list[tuple[str, Path]]:
    requested = list(values)
    if len(requested) > MAX_OPERATIONAL_LOGS:
        raise SanitizedLogExportError("operational_input_count_invalid")
    parsed: list[tuple[str, Path]] = []
    components: set[str] = set()
    for value in requested:
        component, separator, raw_path = value.partition("=")
        if not separator or not COMPONENT_RE.fullmatch(component) or not raw_path:
            raise SanitizedLogExportError("operational_input_spec_invalid")
        if component in components:
            raise SanitizedLogExportError("operational_component_duplicate")
        components.add(component)
        parsed.append((component, Path(raw_path)))
    return parsed


def _read_stable_operational_log(path: Path) -> bytes:
    candidate = _absolute(path)
    _assert_no_symlink_chain(
        candidate,
        missing_code="operational_input_missing",
        symlink_code="operational_input_symlink_not_allowed",
    )
    try:
        expected = candidate.lstat()
    except OSError as exc:
        raise SanitizedLogExportError("operational_input_unreadable") from exc
    if not stat.S_ISREG(expected.st_mode):
        raise SanitizedLogExportError("operational_input_regular_file_required")
    if expected.st_size > MAX_OPERATIONAL_LOG_BYTES:
        raise SanitizedLogExportError("operational_input_too_large")

    descriptor = -1
    chunks: list[bytes] = []
    total = 0
    try:
        descriptor = os.open(candidate, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size) != (
                expected.st_dev,
                expected.st_ino,
                expected.st_size,
            )
        ):
            raise SanitizedLogExportError("operational_input_changed")
        while True:
            chunk = os.read(descriptor, min(READ_CHUNK_BYTES, MAX_OPERATIONAL_LOG_BYTES - total + 1))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_OPERATIONAL_LOG_BYTES:
                raise SanitizedLogExportError("operational_input_too_large")
        finished = os.fstat(descriptor)
        if (
            (finished.st_dev, finished.st_ino, finished.st_size, finished.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            raise SanitizedLogExportError("operational_input_changed")
    except SanitizedLogExportError:
        raise
    except OSError as exc:
        raise SanitizedLogExportError("operational_input_unreadable") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    return b"".join(chunks)


def _severity_counts(lines: Sequence[bytes]) -> dict[str, int]:
    counts = {severity: 0 for severity in SEVERITY_ORDER}
    for line in lines:
        severity = "unclassified"
        explicit = EXPLICIT_LEVEL_RE.search(line)
        if explicit:
            level = explicit.group("level").lower()
            if level in {b"critical", b"fatal", b"panic"}:
                severity = "critical"
            elif level in {b"error", b"err"}:
                severity = "error"
            elif level in {b"warning", b"warn"}:
                severity = "warning"
            elif level == b"info":
                severity = "info"
            else:
                severity = "debug"
        else:
            for candidate, pattern in SEVERITY_PATTERNS:
                if pattern.search(line):
                    severity = candidate
                    break
        counts[severity] += 1
    return counts


def summarize_operational_logs(inputs: Iterable[tuple[str, Path]]) -> list[dict[str, Any]]:
    requested = list(inputs)
    if len(requested) > MAX_OPERATIONAL_LOGS:
        raise SanitizedLogExportError("operational_input_count_invalid")
    components: set[str] = set()
    resolved_paths: set[Path] = set()
    summaries: list[dict[str, Any]] = []
    for component, path in requested:
        if not isinstance(component, str) or not COMPONENT_RE.fullmatch(component):
            raise SanitizedLogExportError("operational_component_invalid")
        if component in components:
            raise SanitizedLogExportError("operational_component_duplicate")
        components.add(component)

        candidate = _absolute(path)
        content = _read_stable_operational_log(candidate)
        try:
            resolved = candidate.resolve(strict=True)
        except OSError as exc:
            raise SanitizedLogExportError("operational_input_changed") from exc
        if resolved in resolved_paths:
            raise SanitizedLogExportError("operational_input_duplicate")
        resolved_paths.add(resolved)
        lines = content.splitlines()
        summaries.append(
            {
                "component": component,
                "byte_count": len(content),
                "line_count": len(lines),
                "severity_counts": _severity_counts(lines),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return sorted(summaries, key=lambda item: str(item["component"]))


def _generated_at(value: datetime | None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise SanitizedLogExportError("generated_at_timezone_required")
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_bundle(
    *,
    audit_paths: Iterable[Path],
    operational_inputs: Iterable[tuple[str, Path]] = (),
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    try:
        records, source_digests = aggregate_security_audit.load_records(audit_paths)
        audit_summary = aggregate_security_audit.aggregate_records(records, source_digests=source_digests)
    except OSError as exc:
        raise SanitizedLogExportError("audit_input_unreadable") from exc
    operational_logs = summarize_operational_logs(operational_inputs)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _generated_at(generated_at),
        "source_contract": {
            "audit_record_schema_version": audit_summary["source_schema_version"],
            "audit_summary_schema_version": aggregate_security_audit.SCHEMA_VERSION,
            "operational_log_count": len(operational_logs),
            "raw_log_messages_included": False,
            "raw_log_paths_included": False,
            "source_file_names_included": False,
        },
        "structured_audit": audit_summary,
        "operational_logs": operational_logs,
    }


def render_markdown(bundle: Mapping[str, Any]) -> str:
    return sanitized_log_contract.render_markdown(bundle)


def _safe_output_root(path: Path) -> Path:
    candidate = _absolute(path)
    parent = candidate.parent
    _assert_no_symlink_chain(
        parent,
        missing_code="output_parent_missing",
        symlink_code="output_symlink_not_allowed",
    )
    try:
        candidate.mkdir(mode=0o700)
    except FileExistsError:
        pass
    except OSError as exc:
        raise SanitizedLogExportError("output_root_invalid") from exc
    try:
        info = candidate.lstat()
    except OSError as exc:
        raise SanitizedLogExportError("output_root_invalid") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SanitizedLogExportError("output_root_invalid")
    return candidate


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_write_exclusive(path: Path, content: bytes) -> Path:
    if path.exists() or path.is_symlink():
        raise SanitizedLogExportError("output_exists")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    published = False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError as exc:
            raise SanitizedLogExportError("output_exists") from exc
        published = True
        _fsync_directory(path.parent)
    except SanitizedLogExportError:
        if published:
            path.unlink(missing_ok=True)
        raise
    except OSError as exc:
        if published:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                raise SanitizedLogExportError("output_cleanup_failed") from exc
        raise SanitizedLogExportError("output_write_failed") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return path


def publish_sanitized_logs(
    *,
    audit_paths: Iterable[Path],
    output_root: Path,
    operational_inputs: Iterable[tuple[str, Path]] = (),
    generated_at: datetime | None = None,
) -> list[Path]:
    bundle = build_bundle(
        audit_paths=audit_paths,
        operational_inputs=operational_inputs,
        generated_at=generated_at,
    )
    json_content = sanitized_log_contract.canonical_json_bytes(bundle)
    markdown_content = render_markdown(bundle).encode("ascii")
    sanitized_log_contract.validate_pair(json_content, markdown_content)
    root = _safe_output_root(output_root)
    outputs = [root / OUTPUT_JSON_NAME, root / OUTPUT_MARKDOWN_NAME]
    if any(path.exists() or path.is_symlink() for path in outputs):
        raise SanitizedLogExportError("output_exists")

    created: list[Path] = []
    try:
        created.append(_atomic_write_exclusive(outputs[0], json_content))
        created.append(_atomic_write_exclusive(outputs[1], markdown_content))
        try:
            findings = check_sanitized_artifacts.scan_paths(created)
        except Exception as exc:
            raise SanitizedLogExportError("sanitized_log_validation_failed") from exc
        if findings:
            raise SanitizedLogExportError("sanitized_log_validation_failed")
        return created
    except BaseException as exc:
        cleanup_failed = False
        for path in reversed(created):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                cleanup_failed = True
        if created:
            try:
                _fsync_directory(root)
            except OSError:
                cleanup_failed = True
        if cleanup_failed:
            raise SanitizedLogExportError("output_cleanup_failed") from exc
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit", type=Path, action="append", required=True, help="explicit structured audit JSONL")
    parser.add_argument(
        "--operational",
        action="append",
        default=[],
        metavar="COMPONENT=PATH",
        help="optional operational log; messages are never copied",
    )
    parser.add_argument("--output-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        publish_sanitized_logs(
            audit_paths=args.audit,
            operational_inputs=parse_operational_specs(args.operational),
            output_root=args.output_root,
        )
        print(json.dumps({"ok": True, "schema_version": SCHEMA_VERSION}, sort_keys=True))
        return 0
    except (SanitizedLogExportError, aggregate_security_audit.AuditAggregationError) as exc:
        print(json.dumps({"ok": False, "error_code": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print(json.dumps({"ok": False, "error_code": "sanitized_log_export_failed"}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
