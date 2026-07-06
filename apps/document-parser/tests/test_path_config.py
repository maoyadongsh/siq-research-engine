from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch


BASE = Path(__file__).resolve().parents[1]
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from path_config import resolve_app_paths  # noqa: E402


def test_default_paths_keep_legacy_data_layout():
    with patch.dict(os.environ, {}, clear=True):
        paths = resolve_app_paths("/tmp/project/apps/document-parser")

    assert paths["data_root"] == Path("/tmp/project/data")
    assert paths["data_dir"] == Path("/tmp/project/data/document-parser")
    assert paths["uploads"] == Path("/tmp/project/data/document-parser/uploads")
    assert paths["results"] == Path("/tmp/project/data/document-parser/results")
    assert paths["output"] == Path("/tmp/project/data/document-parser/output")
    assert paths["db"] == Path("/tmp/project/data/document-parser/db/tasks.db")


def test_runtime_and_artifacts_roots_are_opt_in():
    with patch.dict(
        os.environ,
        {
            "SIQ_DATA_ROOT": "/tmp/state/data",
            "SIQ_RUNTIME_ROOT": "/tmp/state/runtime",
            "SIQ_ARTIFACTS_ROOT": "/tmp/state/artifacts",
        },
        clear=True,
    ):
        paths = resolve_app_paths("/tmp/project/apps/document-parser")

    assert paths["data_root"] == Path("/tmp/state/data")
    assert paths["runtime_root"] == Path("/tmp/state/runtime")
    assert paths["artifacts_root"] == Path("/tmp/state/artifacts")
    assert paths["data_dir"] == Path("/tmp/state/runtime/document-parser")
    assert paths["uploads"] == Path("/tmp/state/runtime/document-parser/uploads")
    assert paths["db"] == Path("/tmp/state/runtime/document-parser/db/tasks.db")
    assert paths["results"] == Path("/tmp/state/artifacts/document-parser/results")
    assert paths["output"] == Path("/tmp/state/artifacts/document-parser/output")
    assert Path("/tmp/project/data/document-parser/results") in paths["results_candidates"]
    assert Path("/tmp/project/data/document-parser/output") in paths["output_candidates"]


def test_legacy_env_overrides_generic_runtime_roots():
    with patch.dict(
        os.environ,
        {
            "SIQ_RUNTIME_ROOT": "/tmp/state/runtime",
            "SIQ_ARTIFACTS_ROOT": "/tmp/state/artifacts",
            "DOCUMENT_PARSE_DATA_DIR": "/tmp/legacy/document-data",
            "DOCUMENT_RESULTS_ROOT": "/tmp/legacy/results",
        },
        clear=True,
    ):
        paths = resolve_app_paths("/tmp/project/apps/document-parser")

    assert paths["data_dir"] == Path("/tmp/legacy/document-data")
    assert paths["uploads"] == Path("/tmp/legacy/document-data/uploads")
    assert paths["results"] == Path("/tmp/legacy/results")


def test_resolver_does_not_create_or_migrate_legacy_data(tmp_path):
    project_root = tmp_path / "project"
    base_dir = project_root / "apps" / "document-parser"
    legacy_result = project_root / "data" / "document-parser" / "results" / "legacy-task" / "document.md"
    base_dir.mkdir(parents=True)
    legacy_result.parent.mkdir(parents=True)
    legacy_result.write_text("# legacy\n", encoding="utf-8")

    state_root = tmp_path / "state"
    with patch.dict(
        os.environ,
        {
            "SIQ_RUNTIME_ROOT": str(state_root / "runtime"),
            "SIQ_ARTIFACTS_ROOT": str(state_root / "artifacts"),
        },
        clear=True,
    ):
        paths = resolve_app_paths(base_dir)

    assert legacy_result.is_file()
    assert not (state_root / "runtime").exists()
    assert not (state_root / "artifacts").exists()
    assert project_root / "data" / "document-parser" / "results" in paths["results_candidates"]
