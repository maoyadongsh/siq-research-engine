"""Path resolution for pdf2md_web runtime data."""

from __future__ import annotations

import os


def _abs_path(path):
    return os.path.abspath(os.path.expanduser(str(path)))


def _env_path(name, default):
    return _abs_path(os.environ.get(name, default))


def resolve_app_paths(base_dir):
    """Resolve runtime paths while keeping legacy layout compatibility.

    By default the historical project-root layout is preserved. Set
    PDF2MD_USE_DATA_LAYOUT=1 or PDF2MD_DATA_DIR=/path/to/data to place new
    runtime data under data/{uploads,results,output,db,cache,logs}.
    Specific legacy environment variables such as UPLOAD_FOLDER still win.
    """

    base_dir = _abs_path(base_dir)
    data_dir = _abs_path(os.environ.get("PDF2MD_DATA_DIR") or os.path.join(base_dir, "data"))
    use_data_layout = (
        bool(os.environ.get("PDF2MD_DATA_DIR"))
        or os.environ.get("PDF2MD_USE_DATA_LAYOUT", "0") == "1"
    )

    uploads_default = os.path.join(data_dir, "uploads") if use_data_layout else os.path.join(base_dir, "uploads")
    results_default = os.path.join(data_dir, "results") if use_data_layout else os.path.join(base_dir, "results")
    output_default = os.path.join(data_dir, "output") if use_data_layout else os.path.join(base_dir, "output")
    db_default = os.path.join(data_dir, "db", "tasks.db") if use_data_layout else os.path.join(base_dir, "tasks.db")
    cache_default = (
        os.path.join(data_dir, "cache", "financial_llm")
        if use_data_layout
        else os.path.join(base_dir, ".financial_llm_cache")
    )
    logs_default = os.path.join(data_dir, "logs") if use_data_layout else base_dir

    return {
        "base_dir": base_dir,
        "data_dir": data_dir,
        "use_data_layout": use_data_layout,
        "uploads": _env_path("UPLOAD_FOLDER", uploads_default),
        "results": _env_path("RESULTS_FOLDER", results_default),
        "output": _env_path("OUTPUT_FOLDER", output_default),
        "db": _env_path("TASK_DB_PATH", db_default),
        "financial_llm_cache": _env_path("FINANCIAL_LLM_CACHE_FOLDER", cache_default),
        "logs": _env_path("PDF2MD_LOG_DIR", logs_default),
    }

