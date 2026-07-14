#!/usr/bin/env python3
"""Verify redacted meeting release reports and bind them to one candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

BUNDLE_SCHEMA_VERSION = "siq.meeting.release-evidence-bundle.v1"
MAX_REPORT_BYTES = 4 * 1024 * 1024
_COMMIT_RE = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
_OPAQUE_REFERENCE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{2,127}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")

REPORT_CONTRACTS = {
    "asr": {
        "schema_version": "siq.meeting.asr-release-evaluation.v2",
        "input_schema_version": "siq.meeting.asr-release-evidence.v2",
        "policy_version": "siq.meeting.asr-release-gates.v2",
        "privacy": {"contains_transcript_text": False, "raw_sensitive_data_emitted": False},
        "keys": frozenset(
            {
                "schema_version",
                "input_schema_version",
                "evaluation_policy_version",
                "source_sha256",
                "candidate",
                "limits",
                "minimum_sample",
                "coverage",
                "metrics",
                "gates",
                "failures",
                "passed",
                "privacy_boundary",
            }
        ),
    },
    "voiceprint": {
        "schema_version": "siq.meeting.voiceprint-release-evaluation.v1",
        "input_schema_version": "siq.meeting.voiceprint-release-evidence.v1",
        "policy_version": "siq.meeting.voiceprint-release-gates.v1",
        "privacy": {"aggregate_only": True, "raw_sensitive_data_emitted": False},
        "keys": frozenset(
            {
                "schema_version",
                "input_schema_version",
                "evaluation_policy_version",
                "source_sha256",
                "candidate",
                "limits",
                "minimum_sample",
                "metrics",
                "gates",
                "failures",
                "suggestion_release_validated",
                "auto_match_validated",
                "release_mode",
                "passed",
                "threshold_policy",
                "environment",
                "privacy_boundary",
            }
        ),
    },
    "performance": {
        "schema_version": "siq.meeting.performance-release-evaluation.v1",
        "input_schema_version": "siq.meeting.performance-release-evidence.v1",
        "policy_version": "siq.meeting.performance-release-gates.v1",
        "privacy": {"aggregate_only": True, "raw_sensitive_data_emitted": False},
        "keys": frozenset(
            {
                "schema_version",
                "input_schema_version",
                "evaluation_policy_version",
                "source_sha256",
                "candidate",
                "limits",
                "minimum_sample",
                "metrics",
                "recovery_counts",
                "gates",
                "failures",
                "passed",
                "privacy_boundary",
            }
        ),
    },
}

_SENSITIVE_KEYS = frozenset(
    {
        "audio_path",
        "storage_path",
        "transcript",
        "reference",
        "hypothesis",
        "speaker_name",
        "user_name",
        "identity",
        "embedding",
        "token",
        "credential",
        "secret",
        "endpoint",
        "url",
    }
)


class BundleVerificationError(ValueError):
    """Raised when a redacted report cannot be accepted as release evidence."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BundleVerificationError("report contains duplicate JSON object keys")
        result[key] = value
    return result


def _load_report(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BundleVerificationError("unable to read a required release report") from exc
    if len(raw) > MAX_REPORT_BYTES:
        raise BundleVerificationError("release report exceeds the size limit")
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except BundleVerificationError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleVerificationError("release report must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise BundleVerificationError("release report must be a JSON object")
    return payload, hashlib.sha256(raw).hexdigest()


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(key.casefold() in _SENSITIVE_KEYS or _contains_sensitive_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def verify(
    reports: dict[str, tuple[dict[str, Any], str]],
    *,
    expected_commit: str,
) -> dict[str, Any]:
    if _COMMIT_RE.fullmatch(expected_commit) is None:
        raise BundleVerificationError("expected candidate must be a lowercase 40- or 64-character commit digest")
    if set(reports) != set(REPORT_CONTRACTS):
        raise BundleVerificationError("ASR, voiceprint, and performance reports are all required")

    environments: set[str] = set()
    receipt_reports: dict[str, dict[str, str]] = {}
    for name, contract in REPORT_CONTRACTS.items():
        report, report_sha256 = reports[name]
        if not isinstance(report_sha256, str) or _SHA256_RE.fullmatch(report_sha256) is None:
            raise BundleVerificationError(f"{name} report digest is invalid")
        if set(report) != contract["keys"]:
            raise BundleVerificationError(f"{name} report fields do not match the redacted schema")
        if _contains_sensitive_key(report):
            raise BundleVerificationError(f"{name} report contains a prohibited sensitive field")
        if report.get("schema_version") != contract["schema_version"]:
            raise BundleVerificationError(f"{name} report schema version is not accepted")
        if report.get("input_schema_version") != contract["input_schema_version"]:
            raise BundleVerificationError(f"{name} input schema version is not accepted")
        if report.get("evaluation_policy_version") != contract["policy_version"]:
            raise BundleVerificationError(f"{name} evaluation policy version is not accepted")
        if report.get("passed") is not True:
            raise BundleVerificationError(f"{name} report has not passed")
        if report.get("failures") != []:
            raise BundleVerificationError(f"{name} report contains blocking failures")
        gates = report.get("gates")
        if not isinstance(gates, dict) or not gates or any(value is not True for value in gates.values()):
            raise BundleVerificationError(f"{name} report gates are incomplete or non-passing")
        if report.get("privacy_boundary") != contract["privacy"]:
            raise BundleVerificationError(f"{name} report privacy boundary is not accepted")
        source_sha256 = report.get("source_sha256")
        if not isinstance(source_sha256, str) or _SHA256_RE.fullmatch(source_sha256) is None:
            raise BundleVerificationError(f"{name} report source digest is invalid")
        candidate = report.get("candidate")
        if not isinstance(candidate, dict) or set(candidate) != {"commit_sha", "environment_profile"}:
            raise BundleVerificationError(f"{name} report candidate binding is invalid")
        if candidate["commit_sha"] != expected_commit:
            raise BundleVerificationError(f"{name} report belongs to a different candidate")
        environment = candidate["environment_profile"]
        if not isinstance(environment, str) or _OPAQUE_REFERENCE_RE.fullmatch(environment) is None:
            raise BundleVerificationError(f"{name} environment profile is invalid")
        environments.add(environment)
        receipt_reports[name] = {
            "schema_version": contract["schema_version"],
            "input_schema_version": contract["input_schema_version"],
            "evaluation_policy_version": contract["policy_version"],
            "report_sha256": report_sha256,
            "source_sha256": source_sha256,
        }

    if len(environments) != 1:
        raise BundleVerificationError("release reports were produced for different environment profiles")
    return {
        "schema_version": BUNDLE_SCHEMA_VERSION,
        "candidate": {
            "commit_sha": expected_commit,
            "environment_profile": environments.pop(),
        },
        "reports": receipt_reports,
        "passed": True,
        "privacy_boundary": {"redacted_reports_only": True, "raw_sensitive_data_emitted": False},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify redacted meeting release reports for one candidate.")
    parser.add_argument("--asr", type=Path, required=True)
    parser.add_argument("--voiceprint", type=Path, required=True)
    parser.add_argument("--performance", type=Path, required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        reports = {
            "asr": _load_report(args.asr),
            "voiceprint": _load_report(args.voiceprint),
            "performance": _load_report(args.performance),
        }
        receipt = verify(reports, expected_commit=args.candidate_commit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except BundleVerificationError as exc:
        parser.exit(1, f"meeting release evidence blocked: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
