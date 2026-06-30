import importlib.util
from pathlib import Path


def test_find_repo_root_falls_back_to_source_tree(tmp_path):
    source = Path(__file__).resolve().parents[1] / "services" / "path_config.py"
    temp_module_path = tmp_path / "apps" / "api" / "services" / "path_config.py"
    temp_module_path.parent.mkdir(parents=True, exist_ok=True)
    temp_module_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    spec = importlib.util.spec_from_file_location("temp_path_config", temp_module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.REPO_ROOT == tmp_path
    assert module.PROJECT_ROOT == tmp_path
