"""Path resolution for PDF parser runtime data."""

from __future__ import annotations

import os


def _abs_path(path):
    return os.path.abspath(os.path.expanduser(str(path)))


def _env_value(*names):
    for name in names:
        value = os.environ.get(name)
        if value and str(value).strip():
            return value
    return None


def _env_has_value(*names):
    return any(bool(os.environ.get(name, "").strip()) for name in names)


def _env_path(names, default):
    if isinstance(names, str):
        names = (names,)
    return _abs_path(_env_value(*names) or default)


def _find_project_root(base_dir):
    current = os.path.abspath(base_dir)
    for path in (current, *iter_parent_dirs(current)):
        if os.path.exists(os.path.join(path, ".git")):
            return path
    return os.path.abspath(os.path.join(base_dir, "..", ".."))


def iter_parent_dirs(path):
    parent = os.path.dirname(path)
    while parent and parent != path:
        yield parent
        path = parent
        parent = os.path.dirname(path)


def _runtime_default(runtime_root, service, legacy_default):
    if _env_has_value("SIQ_RUNTIME_ROOT"):
        return os.path.join(runtime_root, service)
    return legacy_default


def _artifact_default(artifacts_root, service, leaf, legacy_default):
    if _env_has_value("SIQ_ARTIFACTS_ROOT"):
        return os.path.join(artifacts_root, service, leaf)
    return legacy_default


def _unique_paths(*paths):
    ordered = []
    seen = set()
    for path in paths:
        candidate = _abs_path(path)
        if candidate not in seen:
            ordered.append(candidate)
            seen.add(candidate)
    return tuple(ordered)


def resolve_app_paths(base_dir):
    """Resolve runtime paths while keeping legacy layout compatibility.

    By default the monorepo layout stores runtime data under
    data/pdf-parser/{uploads,results,output,db,cache,logs}. Set
    PDF2MD_USE_LEGACY_LAYOUT=1 to use the historical in-app layout.
    Specific legacy environment variables such as UPLOAD_FOLDER still win.
    """

    base_dir = _abs_path(base_dir)
    project_root = _find_project_root(base_dir)
    data_root = _env_path("SIQ_DATA_ROOT", os.path.join(project_root, "data"))
    runtime_root = _env_path("SIQ_RUNTIME_ROOT", os.path.join(project_root, "runtime"))
    artifacts_root = _env_path("SIQ_ARTIFACTS_ROOT", os.path.join(project_root, "artifacts"))
    legacy_data_dir = os.path.join(project_root, "data", "pdf-parser")
    data_dir = _abs_path(
        _env_value("SIQ_PDF2MD_DATA_DIR", "PDF2MD_DATA_DIR", "SIQ_PDF2MD_DATA_DIR")
        or _runtime_default(runtime_root, "pdf-parser", os.path.join(data_root, "pdf-parser"))
    )
    use_data_layout = os.environ.get("PDF2MD_USE_LEGACY_LAYOUT", "0") != "1"

    uploads_default = os.path.join(data_dir, "uploads") if use_data_layout else os.path.join(base_dir, "uploads")
    results_default = (
        _artifact_default(artifacts_root, "pdf-parser", "results", os.path.join(data_dir, "results"))
        if use_data_layout
        else os.path.join(base_dir, "results")
    )
    output_default = (
        _artifact_default(artifacts_root, "pdf-parser", "output", os.path.join(data_dir, "output"))
        if use_data_layout
        else os.path.join(base_dir, "output")
    )
    db_default = os.path.join(data_dir, "db", "tasks.db") if use_data_layout else os.path.join(base_dir, "tasks.db")
    cache_default = (
        os.path.join(data_dir, "cache", "financial_llm")
        if use_data_layout
        else os.path.join(base_dir, ".financial_llm_cache")
    )
    logs_default = os.path.join(data_dir, "logs") if use_data_layout else base_dir
    results = _env_path(("SIQ_PDF_RESULTS_ROOT", "RESULTS_FOLDER", "SIQ_PDF_RESULTS_ROOT"), results_default)
    output = _env_path(("SIQ_PDF_OUTPUT_ROOT", "OUTPUT_FOLDER", "SIQ_PDF_OUTPUT_ROOT"), output_default)

    return {
        "base_dir": base_dir,
        "project_root": project_root,
        "data_root": data_root,
        "runtime_root": runtime_root,
        "artifacts_root": artifacts_root,
        "data_dir": data_dir,
        "legacy_data_dir": _abs_path(legacy_data_dir),
        "use_data_layout": use_data_layout,
        "uploads": _env_path(("SIQ_PDF_UPLOADS_ROOT", "UPLOAD_FOLDER", "SIQ_PDF_UPLOADS_ROOT"), uploads_default),
        "results": results,
        "output": output,
        "db": _env_path(("SIQ_PDF_TASK_DB_PATH", "TASK_DB_PATH", "SIQ_PDF_TASK_DB_PATH"), db_default),
        "financial_llm_cache": _env_path(
            ("SIQ_FINANCIAL_LLM_CACHE_ROOT", "FINANCIAL_LLM_CACHE_FOLDER", "SIQ_FINANCIAL_LLM_CACHE_ROOT"),
            cache_default,
        ),
        "logs": _env_path(("SIQ_PDF2MD_LOG_ROOT", "PDF2MD_LOG_DIR", "SIQ_PDF2MD_LOG_ROOT"), logs_default),
        "results_candidates": _unique_paths(results, os.path.join(data_dir, "results"), os.path.join(legacy_data_dir, "results"), os.path.join(base_dir, "results")),
        "output_candidates": _unique_paths(output, os.path.join(data_dir, "output"), os.path.join(legacy_data_dir, "output"), os.path.join(base_dir, "output")),
    }
