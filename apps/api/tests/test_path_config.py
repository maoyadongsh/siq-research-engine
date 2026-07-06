import importlib.util
import os
from pathlib import Path
from unittest.mock import patch


def _load_path_config(tmp_path, env=None):
    source = Path(__file__).resolve().parents[1] / "services" / "path_config.py"
    temp_module_path = tmp_path / "apps" / "api" / "services" / "path_config.py"
    temp_module_path.parent.mkdir(parents=True, exist_ok=True)
    temp_module_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    spec = importlib.util.spec_from_file_location(f"temp_path_config_{id(tmp_path)}", temp_module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with patch.dict(os.environ, env or {}, clear=True):
        spec.loader.exec_module(module)
    return module


def test_find_repo_root_falls_back_to_source_tree(tmp_path):
    module = _load_path_config(tmp_path)

    assert module.REPO_ROOT == tmp_path
    assert module.PROJECT_ROOT == tmp_path


def test_default_paths_keep_legacy_data_layout(tmp_path):
    module = _load_path_config(tmp_path)

    assert module.DATA_ROOT == tmp_path / "data"
    assert module.BACKEND_DATA_ROOT == tmp_path / "data" / "backend"
    assert module.PDF2MD_DATA_ROOT == tmp_path / "data" / "pdf-parser"
    assert module.PDF_RESULTS_ROOT == tmp_path / "data" / "pdf-parser" / "results"
    assert module.DOCUMENT_PARSER_DATA_ROOT == tmp_path / "data" / "document-parser"
    assert module.DOCUMENT_PARSER_RESULTS_ROOT == tmp_path / "data" / "document-parser" / "results"
    assert module.REPORT_DOWNLOADS_ROOT == tmp_path / "data" / "market-report-finder" / "downloads"


def test_runtime_and_artifacts_roots_are_opt_in(tmp_path):
    state_root = tmp_path / "state"
    module = _load_path_config(
        tmp_path,
        {
            "SIQ_DATA_ROOT": str(state_root / "data"),
            "SIQ_RUNTIME_ROOT": str(state_root / "runtime"),
            "SIQ_ARTIFACTS_ROOT": str(state_root / "artifacts"),
        },
    )

    assert module.DATA_ROOT == state_root / "data"
    assert module.RUNTIME_ROOT == state_root / "runtime"
    assert module.ARTIFACTS_ROOT == state_root / "artifacts"
    assert module.BACKEND_DATA_ROOT == state_root / "runtime" / "api"
    assert module.PDF2MD_DATA_ROOT == state_root / "runtime" / "pdf-parser"
    assert module.DOCUMENT_PARSER_DATA_ROOT == state_root / "runtime" / "document-parser"
    assert module.PDF_RESULTS_ROOT == state_root / "artifacts" / "pdf-parser" / "results"
    assert module.PDF_OUTPUT_ROOT == state_root / "artifacts" / "pdf-parser" / "output"
    assert module.DOCUMENT_PARSER_RESULTS_ROOT == state_root / "artifacts" / "document-parser" / "results"
    assert module.REPORT_DOWNLOADS_ROOT == state_root / "artifacts" / "market-report-finder" / "downloads"
    assert module.WIKI_ROOT == state_root / "data" / "wiki"
    assert tmp_path / "data" / "pdf-parser" / "results" in module.PDF_RESULT_ROOT_CANDIDATES
    assert tmp_path / "data" / "document-parser" / "results" in module.DOCUMENT_PARSER_RESULT_ROOT_CANDIDATES


def test_leaf_and_legacy_env_override_generic_roots(tmp_path):
    state_root = tmp_path / "state"
    module = _load_path_config(
        tmp_path,
        {
            "SIQ_RUNTIME_ROOT": str(state_root / "runtime"),
            "SIQ_ARTIFACTS_ROOT": str(state_root / "artifacts"),
            "PDF_RESULTS_ROOT": str(tmp_path / "legacy-results"),
            "SIQ_BACKEND_DATA_ROOT": str(tmp_path / "backend-data"),
        },
    )

    assert module.BACKEND_DATA_ROOT == tmp_path / "backend-data"
    assert module.PDF_RESULTS_ROOT == tmp_path / "legacy-results"
    assert module.PDF2MD_DATA_ROOT == state_root / "runtime" / "pdf-parser"


def test_path_resolver_does_not_create_or_migrate_data(tmp_path):
    legacy_file = tmp_path / "data" / "pdf-parser" / "results" / "legacy-task" / "result.md"
    legacy_file.parent.mkdir(parents=True)
    legacy_file.write_text("# legacy\n", encoding="utf-8")

    state_root = tmp_path / "state"
    module = _load_path_config(
        tmp_path,
        {
            "SIQ_RUNTIME_ROOT": str(state_root / "runtime"),
            "SIQ_ARTIFACTS_ROOT": str(state_root / "artifacts"),
        },
    )

    assert legacy_file.is_file()
    assert not (state_root / "runtime").exists()
    assert not (state_root / "artifacts").exists()
    assert tmp_path / "data" / "pdf-parser" / "results" in module.PDF_RESULT_ROOT_CANDIDATES
