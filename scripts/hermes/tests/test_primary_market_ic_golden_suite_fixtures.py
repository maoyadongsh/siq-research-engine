from __future__ import annotations

import importlib.util
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SUITE_ROOT = PROJECT_ROOT / "eval_datasets" / "primary_market_ic_real_smoke"
GENERATOR_PATH = SUITE_ROOT / "generate_golden_suite_fixtures.py"
SUITE_MANIFEST_PATH = SUITE_ROOT / "golden_suite_manifest.json"


def _load_generator():
    spec = importlib.util.spec_from_file_location("pmic_golden_suite_fixtures", GENERATOR_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_json(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_golden_candidate_inputs_are_deterministic_independent_and_unaccepted():
    generator = _load_generator()
    observed_deal_ids: set[str] = set()
    observed_source_ids: set[str] = set()
    observed_evidence_ids: set[str] = set()

    base = generator._load_base_generator()
    base_files = base.build_files()
    base._validate_rendered_files(base_files)
    base_snapshot = json.loads(base_files["evidence/evidence_snapshot.json"])
    base_evidence = [
        json.loads(line) for line in base_files["evidence/evidence_items.ndjson"].splitlines() if line.strip()
    ]
    observed_deal_ids.add(base.DEAL_ID)
    observed_source_ids.update(base_snapshot["source_ids"])
    observed_evidence_ids.update(item["evidence_id"] for item in base_evidence)

    for case_id in sorted(generator.SCENARIOS):
        base, files = generator.build_scenario(case_id)
        generator._write_or_check(base, files, check=True)

        manifest = json.loads(files["manifest.json"])
        contract = json.loads(files["fixture_contract.json"])
        snapshot = json.loads(files["evidence/evidence_snapshot.json"])
        evidence = [json.loads(line) for line in files["evidence/evidence_items.ndjson"].splitlines() if line.strip()]

        assert manifest["deal_id"] == contract["deal_id"] == base.DEAL_ID
        assert manifest["golden_case_id"] == contract["golden_case_id"] == case_id
        assert manifest["quality_accepted"] is False
        assert contract["quality_accepted"] is False
        assert manifest["input_only"] is True
        assert contract["input_only"] is True
        assert len(evidence) == 40
        assert all(item["synthetic_evaluation_only"] is True for item in evidence)
        assert all(item["source_class"] == "project_evidence" for item in evidence)
        assert not any(path.startswith(("audit/", "decision/", "release/")) for path in files)

        source_ids = set(snapshot["source_ids"])
        evidence_ids = {item["evidence_id"] for item in evidence}
        assert observed_deal_ids.isdisjoint({base.DEAL_ID})
        assert observed_source_ids.isdisjoint(source_ids)
        assert observed_evidence_ids.isdisjoint(evidence_ids)
        observed_deal_ids.add(base.DEAL_ID)
        observed_source_ids.update(source_ids)
        observed_evidence_ids.update(evidence_ids)

    assert len(observed_deal_ids) == 5
    assert len(observed_source_ids) == 5
    assert len(observed_evidence_ids) == 200


def test_golden_input_manifest_binds_five_distinct_not_run_candidates():
    generator = _load_generator()
    payload = _read_json(SUITE_MANIFEST_PATH)
    cases = payload["cases"]

    assert payload["schema_version"] == "siq_primary_market_ic_golden_input_suite_v1"
    assert payload["synthetic_evaluation_only"] is True
    assert payload["input_only"] is True
    assert payload["quality_accepted"] is False
    assert len(cases) == 5
    assert len({item["case_id"] for item in cases}) == 5
    assert len({item["deal_id"] for item in cases}) == 5
    assert len({item["fixture"] for item in cases}) == 5
    assert all(item["input_status"] == "ready" for item in cases)
    assert all(item["input_only"] is True for item in cases)
    assert all(item["quality_accepted"] is False for item in cases)
    assert all(item["result_status"] == "not_run" for item in cases)
    assert payload == generator.build_suite_manifest()
    identities = [item["input_identity"] for item in cases]
    assert len({item["input_bundle_sha256"] for item in identities}) == 5
    assert len({item["fixture_contract_sha256"] for item in identities}) == 5
    assert len({item["evidence_snapshot_hash"] for item in identities}) == 5
    assert all(item["file_count"] >= 14 for item in identities)
    assert all(
        len(item[key]) == 64
        for item in identities
        for key in (
            "input_bundle_sha256",
            "fixture_contract_sha256",
            "evidence_snapshot_hash",
        )
    )

    expected_cases = {
        "GOLDEN-PMIC-CONDITIONAL-SUPPORT",
        "GOLDEN-PMIC-MATERIAL-RISK",
        "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE",
        "GOLDEN-PMIC-FULL-R3",
        "GOLDEN-PMIC-SNAPSHOT-STALE",
    }
    assert {item["case_id"] for item in cases} == expected_cases


def test_golden_scenarios_encode_missing_risk_debate_and_stale_inputs_without_outputs():
    insufficient = _read_json(SUITE_ROOT / "DEAL-PMIC-INSUFFICIENT-2026" / "fixture_contract.json")
    insufficient_quality = _read_json(
        SUITE_ROOT / "DEAL-PMIC-INSUFFICIENT-2026" / "evidence/evidence_quality_report.json"
    )
    material_risk_items = (SUITE_ROOT / "DEAL-PMIC-MATERIAL-RISK-2026" / "evidence/evidence_items.ndjson").read_text(
        encoding="utf-8"
    )
    full_r3_items = (SUITE_ROOT / "DEAL-PMIC-FULL-R3-2026" / "evidence/evidence_items.ndjson").read_text(
        encoding="utf-8"
    )
    stale = _read_json(SUITE_ROOT / "DEAL-PMIC-SNAPSHOT-STALE-2026" / "scenario_inputs/stale_update.json")

    completeness = insufficient["critical_fact_completeness"]
    assert completeness["status"] == "incomplete"
    assert completeness["missing_critical_facts"] == [
        "customer_and_order_confirmations_missing",
        "audited_financial_statements_missing",
        "freedom_to_operate_opinion_missing",
    ]
    critical_gate = next(
        gate for gate in insufficient_quality["gates"] if gate["id"] == "critical_fact_completeness"
    )
    assert critical_gate == {
        "id": "critical_fact_completeness",
        "status": "warn",
        "message": "3 known critical facts are missing",
    }
    assert "never synthesize R4 after an early terminal" in insufficient["expected_semantics"]["r4"]
    assert "排污许可已经到期" in material_risk_items
    assert "full_red_blue" not in full_r3_items
    assert "支持投资" in full_r3_items and "下行回报不足" in full_r3_items
    assert stale["synthetic_evaluation_only"] is True
    assert stale["requires_existing_human_confirmation"] is True
    assert stale["source"]["source_id"].startswith("PM:DEAL-PMIC-SNAPSHOT-STALE-2026:")
