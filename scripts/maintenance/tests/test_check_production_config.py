from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "check_production_config.py"
spec = importlib.util.spec_from_file_location("check_production_config_under_test", SOURCE)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


def _valid_values() -> dict[str, str]:
    return {
        key: "configured-secret" for key in module.REQUIRED_KEYS
    } | {
        "SIQ_DEPLOYMENT_PROFILE": "production",
        "SIQ_AUTH_SECRET_KEY": "auth-secret-with-at-least-32-characters",
        "SIQ_SOURCE_TOKEN_SECRET": "source-secret-with-at-least-32-characters",
        "SIQ_APP_DATABASE_URL": "postgresql+psycopg://user:password@db/siq_app",
        "REDIS_URL": "redis://redis:6379/0",
        "SIQ_BACKGROUND_JOB_BACKEND": "postgres",
        "SIQ_IC_TASK_LEASE_BACKEND": "postgres",
        "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL": "https://embedding.internal",
        "SIQ_PERFORMANCE_BASELINE_REPORT": "/approved/performance/v2026-07-12/nightly.json",
        "SIQ_HERMES_ASSISTANT_RUNS_URL": "https://hermes.internal/v1/runs",
        "SIQ_AUTH_COOKIE_MODE": "1",
        "SIQ_AUTH_COOKIE_SECURE": "1",
        "SIQ_FINANCIAL_GUARDRAIL_MODE": "block",
        "SIQ_PRODUCTION_CONFIG_REQUIRED": "1",
        "SIQ_LIVE_MODEL_BENCHMARK_MODE": "live-http",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        "SIQ_LIVE_MODEL_URL": "https://hermes.internal/v1/runs",
        "SIQ_LIVE_MODEL_AUTH_TOKEN": "live-secret",
        "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED": "1",
        "SIQ_PERMISSION_NEGATIVE_GATE_SKIP": "0",
        "SIQ_RESTORE_MATRIX_REQUIRED": "1",
        "SIQ_RESTORE_MATRIX_BACKUP_DIR": "/approved/backup/2026-07-13",
        "SIQ_RESTORE_MATRIX_ADMIN_URL": "postgresql://restore:secret@db.internal/postgres",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED": "1",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP": "0",
        "SIQ_PERFORMANCE_COMPARISON_REQUIRED": "1",
    }


def test_valid_production_config_reports_only_statuses():
    report = module.check_config(_valid_values())
    assert report["passed"] is True
    assert all(value == "configured" for value in report["fields"].values())
    assert "password" not in str(report)


def test_production_config_requires_explicit_release_gate_policy():
    values = _valid_values()
    for key in module.RELEASE_GATE_POLICY_KEYS:
        values.pop(key, None)

    report = module.check_config(values)

    assert report["passed"] is False
    assert set(module.RELEASE_GATE_POLICY_KEYS).issubset(set(report["missing"]) | set(report["invalid"]))


def test_placeholder_and_invalid_values_fail_closed():
    values = _valid_values()
    values["SIQ_AUTH_SECRET_KEY"] = "replace-with-secret-manager-value"
    values["SIQ_BACKGROUND_JOB_BACKEND"] = "file"
    values["SIQ_FINANCIAL_GUARDRAIL_MODE"] = "warn"
    report = module.check_config(values)
    assert report["passed"] is False
    assert "SIQ_AUTH_SECRET_KEY" in report["placeholders"]
    assert "SIQ_BACKGROUND_JOB_BACKEND" in report["invalid"]
    assert "SIQ_FINANCIAL_GUARDRAIL_MODE" in report["invalid"]


def test_missing_env_file_is_failure_even_without_required_flag(tmp_path):
    assert module.main(["--env-file", str(tmp_path / "missing.env")]) == 1


def test_cli_json_report_never_prints_config_values(tmp_path, capsys):
    values = _valid_values() | {
        "SIQ_LIVE_MODEL_BENCHMARK_MODE": "live-http",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        "SIQ_LIVE_MODEL_URL": "https://hermes.internal/v1/runs",
        "SIQ_LIVE_MODEL_AUTH_TOKEN": "unique-live-token-value",
    }
    env_file = tmp_path / "production.env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8")

    assert module.main(["--env-file", str(env_file), "--required", "--json"]) == 0
    output = capsys.readouterr().out
    assert "unique-live-token-value" not in output
    assert "auth-secret-with-at-least-32-characters" not in output
    assert "postgresql+psycopg://user:password" not in output


@pytest.mark.parametrize(("mode", "secure"), [("1", "0"), ("true", "false"), ("yes", "off")])
def test_cookie_mode_requires_secure_cookie(mode, secure):
    values = _valid_values()
    values["SIQ_AUTH_COOKIE_MODE"] = mode
    values["SIQ_AUTH_COOKIE_SECURE"] = secure
    report = module.check_config(values)
    assert "SIQ_AUTH_COOKIE_SECURE" in report["invalid"]


def test_cookie_flags_reject_unknown_boolean_values():
    values = _valid_values()
    values["SIQ_AUTH_COOKIE_MODE"] = "maybe"
    values["SIQ_AUTH_COOKIE_SECURE"] = "perhaps"
    report = module.check_config(values)
    assert report["passed"] is False
    assert "SIQ_AUTH_COOKIE_MODE" in report["invalid"]
    assert "SIQ_AUTH_COOKIE_SECURE" in report["invalid"]


def test_required_live_model_gate_requires_endpoint_and_token():
    values = _valid_values() | {
        "SIQ_LIVE_MODEL_BENCHMARK_MODE": "live-http",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        "SIQ_LIVE_MODEL_URL": "https://hermes.internal/v1/runs",
        "SIQ_LIVE_MODEL_AUTH_TOKEN": "live-secret",
    }
    report = module.check_config(values)
    assert report["passed"] is True
    assert report["fields"]["SIQ_LIVE_MODEL_URL"] == "configured"
    assert report["fields"]["SIQ_LIVE_MODEL_AUTH_TOKEN"] == "configured"


def test_required_live_model_gate_rejects_missing_or_placeholder_inputs():
    values = _valid_values() | {
        "SIQ_LIVE_MODEL_BENCHMARK_MODE": "live-http",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        "SIQ_LIVE_MODEL_URL": "https://hermes.internal/v1/runs",
        "SIQ_LIVE_MODEL_AUTH_TOKEN": "replace-with-secret-manager-value",
    }
    report = module.check_config(values)
    assert report["passed"] is False
    assert "SIQ_LIVE_MODEL_AUTH_TOKEN" in report["placeholders"]

    values["SIQ_LIVE_MODEL_AUTH_TOKEN"] = "live-secret"
    values["SIQ_LIVE_MODEL_URL"] = "https://user:secret@hermes.internal/v1/runs"
    report = module.check_config(values)
    assert "SIQ_LIVE_MODEL_URL" in report["invalid"]


def test_required_live_model_gate_rejects_disabled_or_unknown_mode():
    values = _valid_values() | {
        "SIQ_LIVE_MODEL_BENCHMARK_MODE": "disabled",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        "SIQ_LIVE_MODEL_URL": "https://hermes.internal/v1/runs",
        "SIQ_LIVE_MODEL_AUTH_TOKEN": "live-secret",
    }
    report = module.check_config(values)
    assert report["passed"] is False
    assert "SIQ_LIVE_MODEL_BENCHMARK_MODE" in report["invalid"]

    values["SIQ_LIVE_MODEL_BENCHMARK_REQUIRED"] = "maybe"
    report = module.check_config(values)
    assert "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED" in report["invalid"]


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://hermes.internal/v1/runs",
        "https://hermes.internal:/v1/runs",
        "https:///v1/runs",
        "https://user:secret@hermes.internal/v1/runs",
        "https://hermes.internal/v1/runs?access_token=secret",
        "https://hermes.internal/v1/runs#access_token=secret",
    ],
)
def test_live_model_gate_rejects_endpoint_that_could_leak_credentials(endpoint):
    values = _valid_values() | {
        "SIQ_LIVE_MODEL_BENCHMARK_MODE": "live-http",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED": "1",
        "SIQ_LIVE_MODEL_URL": endpoint,
        "SIQ_LIVE_MODEL_AUTH_TOKEN": "live-secret",
    }
    report = module.check_config(values)
    assert report["passed"] is False
    assert "SIQ_LIVE_MODEL_URL" in report["invalid"]
    assert "user:secret" not in str(report)


def test_runtime_rejected_short_signing_secrets_fail_preflight():
    values = _valid_values()
    values["SIQ_AUTH_SECRET_KEY"] = "short"
    values["SIQ_SOURCE_TOKEN_SECRET"] = "also-short"
    report = module.check_config(values)
    assert report["passed"] is False
    assert "SIQ_AUTH_SECRET_KEY" in report["invalid"]
    assert "SIQ_SOURCE_TOKEN_SECRET" in report["invalid"]
    assert "short" not in str(report)

    values = _valid_values()
    values["SIQ_AUTH_SECRET_KEY"] = " " + "a" * 31
    report = module.check_config(values)
    assert "SIQ_AUTH_SECRET_KEY" in report["invalid"]


def test_required_restore_matrix_requires_absolute_backup_and_admin_url():
    values = _valid_values() | {"SIQ_RESTORE_MATRIX_REQUIRED": "1"}
    values.pop("SIQ_RESTORE_MATRIX_BACKUP_DIR")
    values.pop("SIQ_RESTORE_MATRIX_ADMIN_URL")
    report = module.check_config(values)
    assert report["passed"] is False
    assert report["missing"] == ["SIQ_RESTORE_MATRIX_ADMIN_URL", "SIQ_RESTORE_MATRIX_BACKUP_DIR"]

    values.update(
        {
            "SIQ_RESTORE_MATRIX_BACKUP_DIR": "relative/backup",
            "SIQ_RESTORE_MATRIX_ADMIN_URL": "postgresql://restore:secret@db.internal/postgres",
        }
    )
    report = module.check_config(values)
    assert "SIQ_RESTORE_MATRIX_BACKUP_DIR" in report["invalid"]

    values["SIQ_RESTORE_MATRIX_BACKUP_DIR"] = "/approved/backup/2026-07-13"
    report = module.check_config(values)
    assert report["passed"] is True


def test_restore_matrix_rejects_unknown_required_flag_and_malformed_admin_url():
    values = _valid_values() | {"SIQ_RESTORE_MATRIX_REQUIRED": "maybe"}
    report = module.check_config(values)
    assert report["passed"] is False
    assert "SIQ_RESTORE_MATRIX_REQUIRED" in report["invalid"]

    values.update(
        {
            "SIQ_RESTORE_MATRIX_REQUIRED": "1",
            "SIQ_RESTORE_MATRIX_BACKUP_DIR": "/approved/backup/2026-07-13",
            "SIQ_RESTORE_MATRIX_ADMIN_URL": "postgresql:///postgres",
        }
    )
    report = module.check_config(values)
    assert "SIQ_RESTORE_MATRIX_ADMIN_URL" in report["invalid"]


def test_production_release_requires_live_vector_probes_and_versioned_performance_baseline():
    values = _valid_values()
    values["SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED"] = "0"
    values["SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP"] = "1"
    values["SIQ_PERFORMANCE_COMPARISON_REQUIRED"] = "0"
    values["SIQ_PERFORMANCE_BASELINE_REPORT"] = "relative/latest.json"

    report = module.check_config(values)

    assert report["passed"] is False
    assert {
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP",
        "SIQ_PERFORMANCE_COMPARISON_REQUIRED",
        "SIQ_PERFORMANCE_BASELINE_REPORT",
    }.issubset(report["invalid"])


def test_production_performance_baseline_must_be_json_path():
    values = _valid_values()
    values["SIQ_PERFORMANCE_BASELINE_REPORT"] = "/approved/performance/v2026-07-12/nightly.txt"

    report = module.check_config(values)

    assert "SIQ_PERFORMANCE_BASELINE_REPORT" in report["invalid"]
