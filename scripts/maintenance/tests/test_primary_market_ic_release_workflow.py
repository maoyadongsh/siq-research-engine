from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "primary-market-ic-release-gate.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def test_main_ci_calls_primary_market_ic_contract_gate():
    ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "uses: ./.github/workflows/primary-market-ic-release-gate.yml" in ci
    assert "run-release: false" in ci


def test_release_workflow_pins_contract_count_and_v3_gate():
    workflow = _workflow_text()

    assert "Verify all 15 exported IC contracts are current" in workflow
    assert "export_ic_contract_schemas.py --check" in workflow
    assert "run_primary_market_ic_release_gate.py" in workflow
    assert "siq_primary_market_ic_behavior_release_gate_v3" in workflow
    assert "siq_primary_market_ic_behavior_release_gate_v1" not in workflow
    assert "siq_primary_market_ic_behavior_release_gate_v2" not in workflow
    assert "smoke_r1_agent_workflow.py" not in workflow


def test_release_workflow_requires_real_evidence_bindings_and_fails_closed():
    workflow = _workflow_text()

    for variable in (
        "SIQ_PMIC_RELEASE_BUNDLE",
        "SIQ_PMIC_FACTCHECK_REPORT",
        "SIQ_PMIC_REAL_SMOKE_REPORT",
        "SIQ_PMIC_HUMAN_APPROVAL",
    ):
        assert f'require_external("{variable}"' in workflow
    assert 'bundle / "release" / "golden_case_bindings.json"' in workflow
    for flag in (
        "--bundle",
        "--manifest",
        "--profile-matrix",
        "--factcheck-report",
        "--real-smoke-report",
        "--human-approval",
        "--output-json",
        "--output-markdown",
    ):
        assert flag in workflow
    assert "status != 0" in workflow
    assert 'report.get("passed") is not True' in workflow
    assert 'report.get("release_eligible") is not True' in workflow
    assert "uv sync --project apps/api --frozen" in workflow
    assert "uv run --project apps/api --frozen python scripts/maintenance/run_primary_market_ic_release_gate.py" in workflow
    assert "if-no-files-found: error" in workflow
