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
        if value:
            return value
    return None


def _env_path(names: tuple[str, ...], default: Path) -> Path:
    return _abs_path(_env_value(*names) or default)


def resolve_app_paths(base_dir: str | os.PathLike[str]) -> dict[str, Path]:
    base = _abs_path(base_dir)
    project_root = find_project_root(base)
    data_dir = _abs_path(
        _env_value("SIQ_DOCUMENT_PARSE_DATA_DIR", "DOCUMENT_PARSE_DATA_DIR")
        or project_root / "data" / "document-parser"
    )
    return {
        "base_dir": base,
        "project_root": project_root,
        "data_dir": data_dir,
        "uploads": _env_path(("SIQ_DOCUMENT_UPLOADS_ROOT", "DOCUMENT_UPLOADS_ROOT"), data_dir / "uploads"),
        "results": _env_path(("SIQ_DOCUMENT_RESULTS_ROOT", "DOCUMENT_RESULTS_ROOT"), data_dir / "results"),
        "output": _env_path(("SIQ_DOCUMENT_OUTPUT_ROOT", "DOCUMENT_OUTPUT_ROOT"), data_dir / "output"),
        "db": _env_path(("SIQ_DOCUMENT_TASK_DB_PATH", "DOCUMENT_TASK_DB_PATH"), data_dir / "db" / "tasks.db"),
        "logs": _env_path(("SIQ_DOCUMENT_LOG_ROOT", "DOCUMENT_LOG_ROOT"), data_dir / "logs"),
        "cache": _env_path(("SIQ_DOCUMENT_CACHE_ROOT", "DOCUMENT_CACHE_ROOT"), data_dir / "cache"),
    }
