import importlib.util
import json
from pathlib import Path


def _load_module():
    source = Path(__file__).resolve().parents[1] / "export_ic_contract_schemas.py"
    spec = importlib.util.spec_from_file_location("export_ic_contract_schemas_under_test", source)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODEL_ONLY_SCHEMA_IDS = {
    "siq_ic_r1_5_chairman_rulings_v2",
    "siq_ic_r3_debate_turn_v1",
    "siq_ic_r3_debate_verdict_v1",
}

EXPECTED_SCHEMA_COUNT = 15


def test_model_only_phase_contracts_export_from_runtime_registry(tmp_path):
    module = _load_module()

    changed = module.export_schemas(tmp_path)

    assert len(module.EXPECTED_SCHEMA_IDS) == EXPECTED_SCHEMA_COUNT
    assert set(module.SCHEMAS) == module.EXPECTED_SCHEMA_IDS
    assert MODEL_ONLY_SCHEMA_IDS.issubset(module.SCHEMAS)
    assert {path.stem.removesuffix(".schema") for path in changed} == set(module.SCHEMAS)
    for schema_id in MODEL_ONLY_SCHEMA_IDS:
        exported = json.loads((tmp_path / f"{schema_id}.schema.json").read_text(encoding="utf-8"))
        assert exported == module.SCHEMAS[schema_id]
        assert exported["$id"] == schema_id
    assert module.export_schemas(tmp_path, check=True) == []


def test_check_rejects_unexpected_checked_in_contract(tmp_path):
    module = _load_module()
    module.export_schemas(tmp_path)
    legacy = tmp_path / "siq_ic_legacy_v1.schema.json"
    legacy.write_text("{}\n", encoding="utf-8")

    assert module.export_schemas(tmp_path, check=True) == [legacy]


def test_checked_in_ic_contract_exports_are_current():
    module = _load_module()
    contract_dir = (
        module.PROJECT_ROOT
        / "agents"
        / "hermes"
        / "profiles"
        / "siq_ic_shared"
        / "contracts"
    )

    assert module.export_schemas(contract_dir, check=True) == []
