from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.openshell import build_siq_analysis_mount_plan as mount_builder
from scripts.openshell import formal_runtime_contract as contract


def _layout(root: Path, run_id: str) -> tuple[Path, Path, Path]:
    analysis = root / "data/wiki/companies/acme/analysis"
    snapshot = root / mount_builder.SNAPSHOT_ROOT_RELATIVE / run_id
    plan_root = root / mount_builder.PLAN_ROOT_RELATIVE
    analysis.mkdir(parents=True, exist_ok=True)
    snapshot.mkdir(parents=True)
    plan_root.mkdir(parents=True, exist_ok=True)
    mounts = mount_builder._expected_mounts(root, snapshot, analysis)
    content = (json.dumps({"docker": {"mounts": mounts}}, separators=(",", ":"), sort_keys=True) + "\n").encode()
    plan = plan_root / f"{contract.sha256_bytes(content)}.driver-config.json"
    plan.write_bytes(content)
    return analysis, snapshot, plan


def test_normalized_contract_is_stable_across_run_ids(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    first_analysis, first_snapshot, first_plan = _layout(root, "formal-one")
    second_analysis, second_snapshot, second_plan = _layout(root, "formal-two")

    first = contract.normalized_mount_contract(
        project_root=root,
        mount_plan=first_plan,
        analysis_root=first_analysis,
        runtime_snapshot=first_snapshot,
    )
    second = contract.normalized_mount_contract(
        project_root=root,
        mount_plan=second_plan,
        analysis_root=second_analysis,
        runtime_snapshot=second_snapshot,
    )

    assert first["raw_mount_plan_sha256"] != second["raw_mount_plan_sha256"]
    assert first["mount_contract_sha256"] == second["mount_contract_sha256"]
    assert first["projection"]["total_mount_count"] == 12


def test_normalized_contract_rejects_any_plan_relaxation(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    analysis, snapshot, plan = _layout(root, "formal-one")
    payload = json.loads(plan.read_text(encoding="utf-8"))
    payload["docker"]["mounts"][0]["read_only"] = False
    content = (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode()
    tampered = plan.parent / f"{contract.sha256_bytes(content)}.driver-config.json"
    tampered.write_bytes(content)

    with pytest.raises(contract.FormalRuntimeContractError, match="formal_mount_contract_invalid"):
        contract.normalized_mount_contract(
            project_root=root,
            mount_plan=tampered,
            analysis_root=analysis,
            runtime_snapshot=snapshot,
        )


def test_live_mount_validator_requires_exact_counts() -> None:
    expected = {"business_mount_count": 7, "control_mount_count": 5, "total_mount_count": 12}
    assert contract.validate_runtime_mounts(
        context=object(),
        mounts=[],
        validator=lambda _context, _mounts: expected,
    ) == expected
    with pytest.raises(contract.FormalRuntimeContractError, match="formal_live_mount_contract_invalid"):
        contract.validate_runtime_mounts(
            context=object(),
            mounts=[],
            validator=lambda _context, _mounts: {**expected, "control_mount_count": 4},
        )
