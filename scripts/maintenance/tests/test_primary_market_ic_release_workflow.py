from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "primary-market-ic-release-gate.yml"
RUNBOOK_PATH = REPO_ROOT / "docs" / "runbooks" / "primary-market-ic-release-gate.md"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _runbook_text() -> str:
    return RUNBOOK_PATH.read_text(encoding="utf-8")


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


def test_contract_job_covers_offline_golden_evaluation_and_stale_activation():
    contract_job = _workflow_text().split("\n  behavior-release:", maxsplit=1)[0]

    tested_paths = (
        "scripts/maintenance/tests/test_activate_primary_market_ic_stale_fixture.py",
        "scripts/maintenance/tests/test_run_primary_market_ic_golden_evaluator.py",
        "scripts/hermes/tests/test_primary_market_ic_golden_suite_fixtures.py",
    )
    linted_implementations = (
        "eval_datasets/primary_market_ic_real_smoke/generate_golden_suite_fixtures.py",
        "scripts/maintenance/activate_primary_market_ic_stale_fixture.py",
        "scripts/maintenance/run_primary_market_ic_golden_evaluator.py",
    )
    for path in tested_paths:
        assert contract_job.count(path) == 2
    for path in linted_implementations:
        assert contract_job.count(path) == 1
    assert "--real" not in contract_job


def test_runbook_requires_named_two_person_review_and_never_auto_promotes():
    runbook = _runbook_text()

    for marker in (
        "report.create",
        "audit.view",
        "/api/deals/$DEAL_ID/decision/human-confirmation",
        "{\"status\":\"confirmed\",\"dry_run\":true}",
        "{\"status\":\"confirmed\",\"dry_run\":false}",
        "siq_ic_human_confirmation_attestation_v1",
        "siq_ic_human_methodology_approval_v3",
        "golden_case_bindings_sha256",
        "quality_accepted_written: false",
        "candidate_promotion_performed: false",
        "Promotion is a separate pull request",
    ):
        assert marker in runbook
    for case_id in (
        "GOLDEN-PMIC-CONDITIONAL-SUPPORT",
        "GOLDEN-PMIC-MATERIAL-RISK",
        "GOLDEN-PMIC-INSUFFICIENT-EVIDENCE",
        "GOLDEN-PMIC-FULL-R3",
        "GOLDEN-PMIC-SNAPSHOT-STALE",
    ):
        assert case_id in runbook
    assert "never add `confirmed_by`" in runbook
    assert "Do not add an automatic promotion command" in runbook


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
