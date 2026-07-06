"""Runtime path resolution for the generic document parser."""

from __future__ import annotations

import os
from pathlib import Path


def _abs_path(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().resolve()


def iter_parent_dirs(path: Path):
    parent = path.parent
    while parent and parent != path:
        yield parent
        path = parent
        parent = path.parent


def find_project_root(base_dir: str | os.PathLike[str]) -> Path:
    current = _abs_path(base_dir)
    for path in (current, *iter_parent_dirs(current)):
        if (path / ".git").exists():
            return path
    return _abs_path(Path(base_dir) / ".." / "..")


def _env_value(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value
    return None


def _env_has_value(*names: str) -> bool:
    return any(bool(os.environ.get(name, "").strip()) for name in names)


def _env_path(names: tuple[str, ...], default: Path) -> Path:
    return _abs_path(_env_value(*names) or default)


def _runtime_default(runtime_root: Path, service: str, legacy_default: Path) -> Path:
    if _env_has_value("SIQ_RUNTIME_ROOT"):
        return runtime_root / service
    return legacy_default


def _artifact_default(artifacts_root: Path, service: str, leaf: str, legacy_default: Path) -> Path:
    if _env_has_value("SIQ_ARTIFACTS_ROOT"):
        return artifacts_root / service / leaf
    return legacy_default


def _unique_paths(*paths: Path) -> tuple[Path, ...]:
    ordered: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        candidate = _abs_path(path)
        key = str(candidate)
        if key not in seen:
            ordered.append(candidate)
            seen.add(key)
    return tuple(ordered)


def resolve_app_paths(base_dir: str | os.PathLike[str]) -> dict[str, Path]:
    base = _abs_path(base_dir)
    project_root = find_project_root(base)
    data_root = _env_path(("SIQ_DATA_ROOT",), project_root / "data")
    runtime_root = _env_path(("SIQ_RUNTIME_ROOT",), project_root / "runtime")
    artifacts_root = _env_path(("SIQ_ARTIFACTS_ROOT",), project_root / "artifacts")
    legacy_data_dir = project_root / "data" / "document-parser"
    data_dir = _abs_path(
        _env_value("SIQ_DOCUMENT_PARSE_DATA_DIR", "SIQ_DOCUMENT_PARSER_DATA_DIR", "DOCUMENT_PARSE_DATA_DIR", "DOCUMENT_PARSER_DATA_DIR")
        or _runtime_default(runtime_root, "document-parser", data_root / "document-parser")
    )
    results = _env_path(
        ("SIQ_DOCUMENT_PARSE_RESULTS_ROOT", "SIQ_DOCUMENT_PARSER_RESULTS_ROOT", "SIQ_DOCUMENT_RESULTS_ROOT", "DOCUMENT_RESULTS_ROOT"),
        _artifact_default(artifacts_root, "document-parser", "results", data_dir / "results"),
    )
    output = _env_path(
        ("SIQ_DOCUMENT_OUTPUT_ROOT", "DOCUMENT_OUTPUT_ROOT"),
        _artifact_default(artifacts_root, "document-parser", "output", data_dir / "output"),
    )
    return {
        "base_dir": base,
        "project_root": project_root,
        "data_root": data_root,
        "runtime_root": runtime_root,
        "artifacts_root": artifacts_root,
        "data_dir": data_dir,
        "legacy_data_dir": _abs_path(legacy_data_dir),
        "uploads": _env_path(("SIQ_DOCUMENT_UPLOADS_ROOT", "DOCUMENT_UPLOADS_ROOT"), data_dir / "uploads"),
        "results": results,
        "output": output,
        "db": _env_path(("SIQ_DOCUMENT_TASK_DB_PATH", "DOCUMENT_TASK_DB_PATH"), data_dir / "db" / "tasks.db"),
        "logs": _env_path(("SIQ_DOCUMENT_LOG_ROOT", "DOCUMENT_LOG_ROOT"), data_dir / "logs"),
        "cache": _env_path(("SIQ_DOCUMENT_CACHE_ROOT", "DOCUMENT_CACHE_ROOT"), data_dir / "cache"),
        "results_candidates": _unique_paths(results, data_dir / "results", legacy_data_dir / "results"),
        "output_candidates": _unique_paths(output, data_dir / "output", legacy_data_dir / "output"),
    }
