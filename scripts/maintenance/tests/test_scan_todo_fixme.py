import importlib.util
import sys
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1].parent / "scan_todo_fixme.py"
    spec = importlib.util.spec_from_file_location("scan_todo_fixme_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_scan_prunes_runtime_data_and_generated_artifact_dirs_by_default(tmp_path):
    module = _load_module()
    _write(tmp_path / "apps" / "api" / "service.py", "# TODO: refactor service boundary\n")
    _write(tmp_path / "artifacts" / "generated.py", "# TODO: generated artifact\n")
    _write(tmp_path / "data" / "snapshot.py", "# FIXME: runtime data\n")
    _write(tmp_path / "runtimes" / "tool.py", "# TODO: installed runtime\n")
    _write(tmp_path / "var" / "cache.py", "# TODO: transient cache\n")
    _write(tmp_path / "node_modules" / "pkg" / "index.js", "// TODO: dependency\n")

    findings = module.scan(
        tmp_path,
        set(module.DEFAULT_EXCLUDE_DIRS),
        set(module.DEFAULT_EXCLUDE_GLOBS),
    )

    assert [(finding.path, finding.marker) for finding in findings] == [("apps/api/service.py", "TODO")]
