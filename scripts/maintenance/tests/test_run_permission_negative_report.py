from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "scripts" / "maintenance" / "run_permission_negative_report.py"
SPEC = importlib.util.spec_from_file_location("permission_negative_report_under_test", SOURCE)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_manifest_is_explicit_and_covers_required_roles_and_surfaces():
    MODULE.validate_manifest()
    assert {case.surface for case in MODULE.CASES} == MODULE.REQUIRED_SURFACES
    assert {role for case in MODULE.CASES for role in case.roles} == MODULE.REQUIRED_ROLES
    assert all(case.node_id.startswith("tests/") for case in MODULE.CASES)


def test_fake_subprocess_success_has_no_command_output_or_sensitive_values():
    calls = []

    def fake_runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="1 passed in 0.01s\n", stderr="")

    report = MODULE.run_report(
        pytest_command=("fake-pytest",),
        runner=fake_runner,
    )

    assert report["passed"] is True
    assert report["summary"] == {"cases": len(MODULE.CASES), "passed_cases": len(MODULE.CASES), "failed_cases": 0}
    assert report["results"][0]["status"] == "passed"
    assert calls[0][0] == ["fake-pytest", "-q", "--disable-warnings", "--maxfail=1", MODULE.CASES[0].node_id]
    assert "stdout" not in json.dumps(report)
    assert "user" not in json.dumps(report["results"][0]).lower()


def test_fake_subprocess_skip_fails_closed():
    def fake_runner(command, **kwargs):
        return SimpleNamespace(returncode=0, stdout="1 skipped in 0.01s\n", stderr="")

    report = MODULE.run_report(pytest_command=("fake-pytest",), runner=fake_runner)

    assert report["passed"] is False
    assert report["results"][0]["status"] == "skipped"
    assert report["results"][0]["failure_summary"] == "pytest_skipped"


def test_fake_subprocess_failure_is_redacted_and_fail_closed():
    def fake_runner(command, **kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout="FAILED tests/test_x.py - AssertionError: access_token=secret-value https://private.test/task-1\n",
            stderr="",
        )

    report = MODULE.run_report(pytest_command=("fake-pytest",), runner=fake_runner)
    payload = json.dumps(report, ensure_ascii=False)

    assert report["passed"] is False
    assert "secret-value" not in payload
    assert "private.test" not in payload
    assert "task-1" not in payload
    assert report["results"][0]["failure_summary"].startswith("exit_code=1;")
    assert "AssertionError" not in payload


def test_runner_exception_fails_closed_without_exception_details():
    def fake_runner(command, **kwargs):
        raise RuntimeError("token=should-not-appear")

    report = MODULE.run_report(pytest_command=("fake-pytest",), runner=fake_runner)
    payload = json.dumps(report, ensure_ascii=False)

    assert report["passed"] is False
    assert "should-not-appear" not in payload
    assert report["results"][0]["status"] == "failed"


def test_manifest_validation_fails_when_case_node_is_missing():
    with pytest.raises(ValueError, match="manifest_case_missing_required_field"):
        MODULE.validate_manifest(
            tuple(MODULE.CASES[:-1])
            + (
                MODULE.PermissionCase(
                    "broken",
                    "task_write",
                    ("admin",),
                    "expected",
                    "",
                ),
            )
        )
