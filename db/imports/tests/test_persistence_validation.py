import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load():
    path = Path(__file__).resolve().parents[1] / "persistence_validation.py"
    spec = importlib.util.spec_from_file_location("persistence_validation_test_module", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class Validation:
    errors = ["evidence value verification failed", "official issuer source unverified"]


def test_quality_errors_are_preserved_as_persistence_warnings(tmp_path):
    module = _load()
    manifest = {"market": "JP", "company_id": "JP:1", "ticker": "1", "filing_id": "JP:f"}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = module.validate_package_for_persistence(tmp_path, Validation(), market="JP")

    assert result.manifest == manifest
    assert result.warnings == Validation.errors


def test_identity_errors_still_block_persistence(tmp_path):
    module = _load()
    (tmp_path / "manifest.json").write_text(json.dumps({"market": "EU", "ticker": "X"}), encoding="utf-8")

    with pytest.raises(SystemExit, match="company_id, filing_id"):
        module.validate_package_for_persistence(tmp_path, Validation(), market="EU")
