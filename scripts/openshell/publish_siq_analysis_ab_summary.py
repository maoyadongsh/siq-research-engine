#!/usr/bin/env python3
"""Publish one validated private A/B summary as completion evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import (  # noqa: E402
    check_sanitized_artifacts,
    check_v06_completion as completion,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = Path("var/openshell/eval")
SOURCE_NAME = "summary.json"
OUTPUT_JSON = Path("artifacts/openshell/v0.6/formal-ab-summary.sanitized.json")
OUTPUT_MARKDOWN = Path("artifacts/openshell/v0.6/formal-ab-summary.sanitized.md")
MAX_SUMMARY_BYTES = 8 * 1024 * 1024
FORBIDDEN_EVALUATION_RE = re.compile(r"(?:synthetic|fixture|fake|test)", re.IGNORECASE)


class AbSummaryPublishError(RuntimeError):
    """Stable failure that never includes private content or machine paths."""


@dataclass(frozen=True)
class FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int
    ctime_ns: int


@dataclass(frozen=True)
class SourceSnapshot:
    path: Path
    identity: FileIdentity
    payload: Mapping[str, Any]


def _identity(info: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=info.st_dev,
        inode=info.st_ino,
        size=info.st_size,
        mtime_ns=info.st_mtime_ns,
        ctime_ns=info.st_ctime_ns,
    )


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AbSummaryPublishError("ab_summary_duplicate_json_key")
        result[key] = value
    return result


def _project_root(value: Path) -> Path:
    try:
        root = value.expanduser().resolve(strict=True)
        info = root.stat()
    except OSError as exc:
        raise AbSummaryPublishError("project_root_invalid") from exc
    if not stat.S_ISDIR(info.st_mode):
        raise AbSummaryPublishError("project_root_invalid")
    return root


def _reject_symlink_components(root: Path, relative: Path) -> Path:
    current = root
    for index, component in enumerate(relative.parts):
        current /= component
        try:
            info = current.lstat()
        except OSError as exc:
            raise AbSummaryPublishError("ab_summary_path_invalid") from exc
        if stat.S_ISLNK(info.st_mode):
            raise AbSummaryPublishError("ab_summary_path_invalid")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise AbSummaryPublishError("ab_summary_path_invalid")
    return current


def _read_descriptor(descriptor: int) -> bytes:
    content = bytearray()
    while True:
        chunk = os.read(descriptor, min(64 * 1024, MAX_SUMMARY_BYTES + 1 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > MAX_SUMMARY_BYTES:
            raise AbSummaryPublishError("ab_summary_source_invalid")
    return bytes(content)


def _read_source(root: Path, evaluation_id: str) -> SourceSnapshot:
    if (
        not completion._is_safe_id(evaluation_id)
        or FORBIDDEN_EVALUATION_RE.search(evaluation_id)
    ):
        raise AbSummaryPublishError("ab_summary_evaluation_id_invalid")
    relative = SOURCE_ROOT / evaluation_id / SOURCE_NAME
    path = _reject_symlink_components(root, relative)
    descriptor = -1
    try:
        initial = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(initial.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or initial.st_uid != os.geteuid()
            or opened.st_uid != os.geteuid()
            or initial.st_nlink != 1
            or opened.st_nlink != 1
            or stat.S_IMODE(initial.st_mode) != 0o600
            or stat.S_IMODE(opened.st_mode) != 0o600
            or not 0 < opened.st_size <= MAX_SUMMARY_BYTES
            or _identity(initial) != _identity(opened)
        ):
            raise AbSummaryPublishError("ab_summary_source_invalid")
        source_identity = _identity(opened)
        content = _read_descriptor(descriptor)
        finished = os.fstat(descriptor)
        final = path.lstat()
        if source_identity != _identity(finished) or source_identity != _identity(final):
            raise AbSummaryPublishError("ab_summary_source_changed")
    except AbSummaryPublishError:
        raise
    except OSError as exc:
        raise AbSummaryPublishError("ab_summary_source_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)

    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except AbSummaryPublishError:
        raise
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise AbSummaryPublishError("ab_summary_json_invalid") from exc
    if not isinstance(payload, dict):
        raise AbSummaryPublishError("ab_summary_json_invalid")
    canonical = _canonical_json(payload)
    if content != canonical:
        raise AbSummaryPublishError("ab_summary_source_not_canonical")
    if check_sanitized_artifacts.scan_content(OUTPUT_JSON, canonical):
        raise AbSummaryPublishError("ab_summary_not_sanitized")
    if payload.get("evaluation_id") != evaluation_id or not completion._validate_ab_summary(payload):
        raise AbSummaryPublishError("ab_summary_schema_invalid")
    return SourceSnapshot(path=path, identity=source_identity, payload=payload)


def _source_unchanged(snapshot: SourceSnapshot) -> None:
    try:
        info = snapshot.path.lstat()
    except OSError as exc:
        raise AbSummaryPublishError("ab_summary_source_changed") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or _identity(info) != snapshot.identity
    ):
        raise AbSummaryPublishError("ab_summary_source_changed")


def _render_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _markdown(payload: Mapping[str, Any]) -> bytes:
    arms = payload["arms"]
    comparison = payload["comparison"]
    deltas = comparison["metric_deltas"]
    quality = payload["quality_gate"]
    lines = [
        "# SIQ OpenShell Formal A/B Summary",
        "",
        f"- Schema: `{payload['schema_version']}`",
        f"- Evaluation: `{payload['evaluation_id']}`",
        f"- Cases: `{payload['case_count']}`",
        f"- Repetitions: `{payload['repetitions']}`",
        f"- Executions: `{payload['execution_count']}`",
        f"- Quality gate: `{'GO' if quality['passed'] else 'NO_GO'}`",
        f"- Cutover performed: `{str(quality['cutover_performed']).lower()}`",
        "",
        "## Quality Metrics",
        "",
        "| Metric | Host | OpenShell | Delta |",
        "|---|---:|---:|---:|",
    ]
    for metric in completion.AB_COMPARISON_METRICS:
        lines.append(
            f"| `{metric}` | {_render_value(arms['host'][metric])} | "
            f"{_render_value(arms['openshell'][metric])} | {_render_value(deltas[metric])} |"
        )
    lines.extend(
        [
            "",
            "## Latency",
            "",
            "| Arm | TTFT P50 | TTFT P95 | Total P50 | Total P95 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for arm_name in ("host", "openshell"):
        latency = arms[arm_name]["latency_ms"]
        lines.append(
            f"| `{arm_name}` | {_render_value(latency['ttft_p50'])} | "
            f"{_render_value(latency['ttft_p95'])} | {_render_value(latency['total_p50'])} | "
            f"{_render_value(latency['total_p95'])} |"
        )
    lines.extend(
        [
            "",
            f"Total P95 ratio: `{_render_value(comparison['total_p95_ratio'])}`",
            "",
            "Failure reasons: "
            + (", ".join(f"`{reason}`" for reason in quality["failure_reasons"]) or "`none`"),
            "",
        ]
    )
    return "\n".join(lines).encode("ascii")


def _output_paths(root: Path) -> tuple[Path, Path]:
    paths: list[Path] = []
    for relative in (OUTPUT_JSON, OUTPUT_MARKDOWN):
        parent = _reject_symlink_components(root, relative.parent)
        try:
            info = parent.lstat()
        except OSError as exc:
            raise AbSummaryPublishError("ab_summary_output_parent_invalid") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o002
        ):
            raise AbSummaryPublishError("ab_summary_output_parent_invalid")
        paths.append(root / relative)
    return paths[0], paths[1]


def _validate_existing(path: Path, *, replace: bool) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise AbSummaryPublishError("ab_summary_output_invalid") from exc
    if not replace:
        raise AbSummaryPublishError("ab_summary_output_exists")
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
    ):
        raise AbSummaryPublishError("ab_summary_output_invalid")


def _stage(path: Path, content: bytes) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _install_outputs(
    outputs: Sequence[tuple[Path, bytes]],
    *,
    replace: bool,
) -> None:
    for path, _content in outputs:
        _validate_existing(path, replace=replace)
    staged: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        staged = [(path, _stage(path, content)) for path, content in outputs]
        for path, temporary in staged:
            if replace:
                os.replace(temporary, path)
            else:
                try:
                    os.link(temporary, path, follow_symlinks=False)
                except FileExistsError as exc:
                    raise AbSummaryPublishError("ab_summary_output_exists") from exc
                temporary.unlink()
            installed.append(path)
        directory_fd = os.open(outputs[0][0].parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        if not replace:
            for path in installed:
                path.unlink(missing_ok=True)
        raise
    finally:
        for _path, temporary in staged:
            temporary.unlink(missing_ok=True)


def _verify_output(path: Path, expected: bytes) -> None:
    try:
        info = path.lstat()
        content = path.read_bytes()
    except OSError as exc:
        raise AbSummaryPublishError("ab_summary_output_invalid") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.geteuid()
        or info.st_nlink != 1
        or stat.S_IMODE(info.st_mode) != 0o600
        or content != expected
    ):
        raise AbSummaryPublishError("ab_summary_output_invalid")


def publish_summary(
    *,
    project_root: Path,
    evaluation_id: str,
    replace: bool = False,
) -> tuple[Path, Path]:
    root = _project_root(project_root)
    snapshot = _read_source(root, evaluation_id)
    json_path, markdown_path = _output_paths(root)
    json_content = _canonical_json(snapshot.payload)
    markdown_content = _markdown(snapshot.payload)
    findings = check_sanitized_artifacts.scan_content(json_path, json_content)
    findings.extend(check_sanitized_artifacts.scan_content(markdown_path, markdown_content))
    if findings:
        raise AbSummaryPublishError("ab_summary_not_sanitized")
    _source_unchanged(snapshot)
    outputs = ((json_path, json_content), (markdown_path, markdown_content))
    _install_outputs(outputs, replace=replace)
    for path, expected in outputs:
        _verify_output(path, expected)
    if check_sanitized_artifacts.scan_paths([json_path, markdown_path]):
        raise AbSummaryPublishError("ab_summary_not_sanitized")
    return json_path, markdown_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--replace", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        json_path, markdown_path = publish_summary(
            project_root=args.project_root,
            evaluation_id=args.evaluation_id,
            replace=args.replace,
        )
    except (AbSummaryPublishError, OSError, ValueError) as exc:
        code = str(exc) if isinstance(exc, AbSummaryPublishError) else "ab_summary_publish_failed"
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "ok": True,
                "schema_version": completion.AB_SUMMARY_SCHEMA,
                "json": json_path.relative_to(_project_root(args.project_root)).as_posix(),
                "markdown": markdown_path.relative_to(_project_root(args.project_root)).as_posix(),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
