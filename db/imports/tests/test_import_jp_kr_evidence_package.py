import importlib.util
from pathlib import Path


def _load(name: str):
    path = Path(__file__).resolve().parents[1] / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_jp_importer_rejects_wrong_schema():
    module = _load("import_jp_evidence_package_to_postgres")
    try:
        module.validate_schema("pdf2md")
    except SystemExit as exc:
        assert "edinet_jp" in str(exc)
    else:
        raise AssertionError("JP importer should reject pdf2md")


def test_kr_importer_rejects_wrong_schema():
    module = _load("import_kr_evidence_package_to_postgres")
    try:
        module.validate_schema("pdf2md")
    except SystemExit as exc:
        assert "dart_kr" in str(exc)
    else:
        raise AssertionError("KR importer should reject pdf2md")
