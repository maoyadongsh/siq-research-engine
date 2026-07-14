#!/usr/bin/env python3
"""Validate production configuration without printing secret values."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SCHEMA_VERSION = "siq_production_config_preflight_v1"
PLACEHOLDER_RE = re.compile(r"(?i)(replace-with|example\.internal|example\.com|changeme|your[-_]|<[^>]+>)")
REQUIRED_KEYS = (
    "SIQ_DEPLOYMENT_PROFILE",
    "SIQ_AUTH_SECRET_KEY",
    "SIQ_SOURCE_TOKEN_SECRET",
    "SIQ_METRICS_TOKEN",
    "SIQ_APP_DATABASE_URL",
    "REDIS_URL",
    "SIQ_BACKGROUND_JOB_BACKEND",
    "SIQ_IC_TASK_LEASE_BACKEND",
    "SIQ_AGENT_MEMORY_MILVUS_COLLECTION",
    "SIQ_AGENT_MEMORY_EMBEDDING_BASE_URL",
    "HERMES_API_KEY",
    "SIQ_HERMES_ASSISTANT_RUNS_URL",
    "SIQ_MARKET_REPORT_FINDER_TOKEN",
    "SIQ_MARKET_REPORT_RULES_TOKEN",
    "SIQ_PERFORMANCE_BASELINE_REPORT",
)
LIVE_REQUIRED_KEYS = ("SIQ_LIVE_MODEL_URL", "SIQ_LIVE_MODEL_AUTH_TOKEN")
RESTORE_REQUIRED_KEYS = ("SIQ_RESTORE_MATRIX_BACKUP_DIR", "SIQ_RESTORE_MATRIX_ADMIN_URL")
RELEASE_GATE_POLICY_KEYS = (
    "SIQ_PRODUCTION_CONFIG_REQUIRED",
    "SIQ_LIVE_MODEL_BENCHMARK_MODE",
    "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED",
    "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED",
    "SIQ_PERMISSION_NEGATIVE_GATE_SKIP",
    "SIQ_RESTORE_MATRIX_REQUIRED",
    "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED",
    "SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP",
    "SIQ_PERFORMANCE_COMPARISON_REQUIRED",
)
TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
FALSEY_VALUES = frozenset({"0", "false", "no", "off"})
BOOLEAN_VALUES = TRUTHY_VALUES | FALSEY_VALUES
MINIMUM_SECRET_LENGTHS = {
    "SIQ_AUTH_SECRET_KEY": 32,
    "SIQ_SOURCE_TOKEN_SECRET": 32,
}


def _parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value.strip().strip('"\'')
    return values


def _status(key: str, value: str | None) -> str:
    if value in (None, ""):
        return "missing"
    if PLACEHOLDER_RE.search(value):
        return "placeholder"
    return "configured"


def _normalized(value: str | None) -> str:
    return (value or "").strip().lower()


def _is_truthy(value: str | None) -> bool:
    return _normalized(value) in TRUTHY_VALUES


def _is_falsey(value: str | None) -> bool:
    return _normalized(value) in FALSEY_VALUES


def _valid_url(value: str, *, schemes: set[str], require_tls: bool = False) -> bool:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    if parsed.scheme not in schemes or not parsed.hostname or port is None and parsed.netloc.endswith(":"):
        return False
    if require_tls and parsed.scheme != "https":
        return False
    return parsed.username is None and parsed.password is None and not parsed.query and not parsed.fragment


def _invalid_fields(values: dict[str, str], statuses: dict[str, str]) -> list[str]:
    invalid: set[str] = set()
    if values.get("SIQ_DEPLOYMENT_PROFILE", "").lower() not in {"production", "prod"}:
        invalid.add("SIQ_DEPLOYMENT_PROFILE")
    if statuses.get("SIQ_APP_DATABASE_URL") == "configured":
        try:
            database_url = urlsplit(values["SIQ_APP_DATABASE_URL"])
            database_port = database_url.port
        except ValueError:
            database_url = None
            database_port = None
        if (
            database_url is None
            or database_url.scheme not in {"postgresql", "postgresql+psycopg"}
            or not database_url.hostname
            or not database_url.path.strip("/")
            or database_port is None and database_url.netloc.endswith(":")
        ):
            invalid.add("SIQ_APP_DATABASE_URL")
    if values.get("SIQ_BACKGROUND_JOB_BACKEND") != "postgres":
        invalid.add("SIQ_BACKGROUND_JOB_BACKEND")
    if values.get("SIQ_IC_TASK_LEASE_BACKEND") != "postgres":
        invalid.add("SIQ_IC_TASK_LEASE_BACKEND")
    cookie_mode = _normalized(values.get("SIQ_AUTH_COOKIE_MODE"))
    cookie_secure = _normalized(values.get("SIQ_AUTH_COOKIE_SECURE"))
    if cookie_mode and cookie_mode not in BOOLEAN_VALUES:
        invalid.add("SIQ_AUTH_COOKIE_MODE")
    if cookie_secure and cookie_secure not in BOOLEAN_VALUES:
        invalid.add("SIQ_AUTH_COOKIE_SECURE")
    if _is_truthy(cookie_mode) and not _is_truthy(cookie_secure):
        invalid.add("SIQ_AUTH_COOKIE_SECURE")
    if values.get("SIQ_FINANCIAL_GUARDRAIL_MODE") != "block":
        invalid.add("SIQ_FINANCIAL_GUARDRAIL_MODE")
    for key, minimum in MINIMUM_SECRET_LENGTHS.items():
        if statuses.get(key) == "configured" and len(values.get(key, "").strip()) < minimum:
            invalid.add(key)

    live_mode = _normalized(values.get("SIQ_LIVE_MODEL_BENCHMARK_MODE"))
    live_required_value = _normalized(values.get("SIQ_LIVE_MODEL_BENCHMARK_REQUIRED"))
    if live_mode and live_mode not in {"disabled", "live-http"}:
        invalid.add("SIQ_LIVE_MODEL_BENCHMARK_MODE")
    if live_required_value and live_required_value not in BOOLEAN_VALUES:
        invalid.add("SIQ_LIVE_MODEL_BENCHMARK_REQUIRED")
    if _is_truthy(live_required_value) and live_mode != "live-http":
        invalid.add("SIQ_LIVE_MODEL_BENCHMARK_MODE")
    live_required = live_mode == "live-http" or _is_truthy(live_required_value)
    if live_required:
        endpoint = values.get("SIQ_LIVE_MODEL_URL", "").strip()
        if statuses.get("SIQ_LIVE_MODEL_URL") == "configured" and not _valid_url(
            endpoint, schemes={"https"}, require_tls=True
        ):
            invalid.add("SIQ_LIVE_MODEL_URL")

    restore_required_value = _normalized(values.get("SIQ_RESTORE_MATRIX_REQUIRED"))
    if restore_required_value and restore_required_value not in BOOLEAN_VALUES:
        invalid.add("SIQ_RESTORE_MATRIX_REQUIRED")
    restore_requested = _is_truthy(restore_required_value) or bool(values.get("SIQ_RESTORE_MATRIX_BACKUP_DIR", "").strip())
    if restore_requested:
        backup_dir = values.get("SIQ_RESTORE_MATRIX_BACKUP_DIR", "").strip()
        admin_url = values.get("SIQ_RESTORE_MATRIX_ADMIN_URL", "").strip()
        if statuses.get("SIQ_RESTORE_MATRIX_BACKUP_DIR") == "configured" and not Path(backup_dir).is_absolute():
            invalid.add("SIQ_RESTORE_MATRIX_BACKUP_DIR")
        if statuses.get("SIQ_RESTORE_MATRIX_ADMIN_URL") == "configured":
            try:
                parsed_admin = urlsplit(admin_url)
                admin_port = parsed_admin.port
            except ValueError:
                parsed_admin = None
                admin_port = None
            if (
                parsed_admin is None
                or parsed_admin.scheme not in {"postgresql", "postgresql+psycopg"}
                or not parsed_admin.hostname
                or not parsed_admin.path.strip("/")
                or admin_port is None and parsed_admin.netloc.endswith(":")
            ):
                invalid.add("SIQ_RESTORE_MATRIX_ADMIN_URL")
    performance_baseline = values.get("SIQ_PERFORMANCE_BASELINE_REPORT", "").strip()
    if statuses.get("SIQ_PERFORMANCE_BASELINE_REPORT") == "configured":
        baseline_path = Path(performance_baseline)
        if not baseline_path.is_absolute() or baseline_path.suffix.lower() != ".json":
            invalid.add("SIQ_PERFORMANCE_BASELINE_REPORT")
    for key in (
        "SIQ_PRODUCTION_CONFIG_REQUIRED",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED",
        "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED",
        "SIQ_PERMISSION_NEGATIVE_GATE_SKIP",
        "SIQ_RESTORE_MATRIX_REQUIRED",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP",
        "SIQ_PERFORMANCE_COMPARISON_REQUIRED",
    ):
        value = _normalized(values.get(key))
        if value and value not in BOOLEAN_VALUES:
            invalid.add(key)
    for key in (
        "SIQ_PRODUCTION_CONFIG_REQUIRED",
        "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED",
        "SIQ_PERMISSION_NEGATIVE_GATE_REQUIRED",
        "SIQ_RESTORE_MATRIX_REQUIRED",
        "SIQ_AGENT_MEMORY_VECTOR_PROBES_REQUIRED",
        "SIQ_PERFORMANCE_COMPARISON_REQUIRED",
    ):
        if not _is_truthy(values.get(key)):
            invalid.add(key)
    if not _is_falsey(values.get("SIQ_PERMISSION_NEGATIVE_GATE_SKIP")):
        invalid.add("SIQ_PERMISSION_NEGATIVE_GATE_SKIP")
    if not _is_falsey(values.get("SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP")):
        invalid.add("SIQ_AGENT_MEMORY_VECTOR_PROBES_SKIP")
    return sorted(invalid)


def _conditional_statuses(values: dict[str, str]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    live_required = _normalized(values.get("SIQ_LIVE_MODEL_BENCHMARK_MODE")) == "live-http" or _is_truthy(
        values.get("SIQ_LIVE_MODEL_BENCHMARK_REQUIRED")
    )
    if live_required:
        statuses.update({key: _status(key, values.get(key)) for key in LIVE_REQUIRED_KEYS})
    restore_requested = _is_truthy(values.get("SIQ_RESTORE_MATRIX_REQUIRED")) or bool(
        values.get("SIQ_RESTORE_MATRIX_BACKUP_DIR", "").strip()
    )
    if restore_requested:
        statuses.update({key: _status(key, values.get(key)) for key in RESTORE_REQUIRED_KEYS})
    return statuses


def check_config(values: dict[str, str]) -> dict[str, Any]:
    statuses = {key: _status(key, values.get(key)) for key in (*REQUIRED_KEYS, *RELEASE_GATE_POLICY_KEYS)}
    conditional = _conditional_statuses(values)
    statuses.update(conditional)
    missing = sorted(key for key, status in statuses.items() if status == "missing")
    placeholders = sorted(key for key, status in statuses.items() if status == "placeholder")
    invalid = sorted(_invalid_fields(values, statuses))
    passed = not missing and not placeholders and not invalid
    return {
        "schema_version": SCHEMA_VERSION,
        "passed": passed,
        "summary": {"required": len(statuses), "missing": len(missing), "placeholder": len(placeholders), "invalid": len(invalid)},
        "fields": statuses,
        "missing": missing,
        "placeholders": placeholders,
        "invalid": invalid,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=None)
    parser.add_argument("--required", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    path = args.env_file
    if path is None:
        values = {
            key: os.environ.get(key, "")
            for key in set(REQUIRED_KEYS)
            | {
                "SIQ_AUTH_COOKIE_MODE",
                "SIQ_AUTH_COOKIE_SECURE",
                "SIQ_FINANCIAL_GUARDRAIL_MODE",
                "SIQ_LIVE_MODEL_BENCHMARK_MODE",
                "SIQ_LIVE_MODEL_BENCHMARK_REQUIRED",
                "SIQ_RESTORE_MATRIX_REQUIRED",
                *RESTORE_REQUIRED_KEYS,
                *LIVE_REQUIRED_KEYS,
                *RELEASE_GATE_POLICY_KEYS,
            }
        }
    elif not path.is_file():
        report = {"schema_version": SCHEMA_VERSION, "passed": False, "reason": "env_file_missing", "fields": {}}
        print(json.dumps(report, ensure_ascii=False))
        return 1
    else:
        values = _parse_env_file(path)
    report = check_config(values)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"Production config preflight: {'PASS' if report['passed'] else 'FAIL'}")
        print(json.dumps(report["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if report["passed"] or not args.required else 1


if __name__ == "__main__":
    raise SystemExit(main())
