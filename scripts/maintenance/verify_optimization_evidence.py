#!/usr/bin/env python3
"""Verify optimization evidence metadata, redaction, and artifact checksums."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

try:
    from evidence_metadata import attach_evidence_metadata, sha256_file
except ModuleNotFoundError:  # pragma: no cover - import style used by test loaders
    from scripts.maintenance.evidence_metadata import attach_evidence_metadata, sha256_file

SCHEMA_VERSION = "siq_optimization_evidence_verification_v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_FIELDS = (
    "generated_at",
    "base_commit",
    "worktree_dirty",
    "task_id",
    "environment_profile",
    "command",
    "result",
    "duration_seconds",
    "failures",
    "artifact_checksums",
)
STRING_FIELDS = {
    "generated_at",
    "base_commit",
    "task_id",
    "environment_profile",
    "command",
    "result",
}
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
SAFE_FIELD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,79}\Z")
SAFE_ARTIFACT_RE = re.compile(r"[A-Za-z0-9_./<>@+=:#-]{1,240}\Z")
LOCAL_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9])/(?:home|tmp)(?=/|\b)|[A-Za-z]:\\+Users\\+",
    re.IGNORECASE,
)
DSN_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp)://",
    re.IGNORECASE,
)
URI_USERINFO_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s/:@]+:[^\s/@]+@", re.IGNORECASE)
QUERY_SECRET_RE = re.compile(
    r"[?&](?:access_token|api[_-]?key|auth[_-]?token|password|secret|token)=([^&#\s]+)",
    re.IGNORECASE,
)
SECRET_ASSIGNMENT_RE = re.compile(
    r"(?:--|\b)(?:access[-_]?token|api[-_]?key|auth[-_]?token|authorization|cookie|"
    r"database[-_]?url|dsn|password|passwd|secret|token)(?:=|\s+)([^\s,;]+)",
    re.IGNORECASE,
)
AUTH_HEADER_RE = re.compile(r"\b(?:authorization|cookie)\s*:\s*([^\r\n]+)", re.IGNORECASE)
SAFE_CREDENTIAL_VALUES = {
    "",
    "***",
    "configured",
    "environment",
    "invalid",
    "missing",
    "none",
    "not_configured",
    "placeholder",
    "redacted",
    "secret_manager",
    "unavailable",
    "unknown",
    "unset",
}
SENSITIVE_METADATA_SUFFIXES = (
    "_available",
    "_configured",
    "_count",
    "_error",
    "_error_type",
    "_field",
    "_fields",
    "_manager",
    "_name",
    "_names",
    "_present",
    "_provider",
    "_required",
    "_source",
    "_state",
    "_status",
)


def _display_path(path: Path, repo_root: Path) -> str:
    try:
        display = path.resolve().relative_to(repo_root.resolve()).as_posix()
    except (OSError, ValueError):
        display = f"<external>/{path.name}"
    return _safe_artifact_label(display)


def _safe_artifact_label(value: str) -> str:
    if (
        SAFE_ARTIFACT_RE.fullmatch(value)
        and not value.startswith("/")
        and not LOCAL_ABSOLUTE_PATH_RE.search(value)
    ):
        return value
    fingerprint = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"<unsafe-artifact-key:{fingerprint}>"


def _field_path(parent: str, key: Any) -> str:
    text = str(key)
    if _is_sensitive_key(text):
        return f"{parent}.<credential-field>"
    if SAFE_FIELD_RE.fullmatch(text):
        return f"{parent}.{text}"
    return f"{parent}.<field>"


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    if not normalized:
        return False
    if normalized.startswith(("missing_", "required_")) or normalized.endswith(
        SENSITIVE_METADATA_SUFFIXES
    ):
        return False
    direct_markers = (
        "api_key",
        "authorization",
        "client_secret",
        "connection_string",
        "cookie",
        "database_url",
        "password",
        "passwd",
        "secret_key",
    )
    if any(marker in normalized for marker in direct_markers):
        return True
    if normalized in {"credential", "credentials", "dsn", "secret", "token"}:
        return True
    return any(
        marker in normalized
        for marker in ("access_token", "api_token", "auth_token", "bearer_token", "refresh_token")
    )


def _is_redacted_credential(value: str) -> bool:
    normalized = value.strip().strip("'\"").lower()
    if normalized in SAFE_CREDENTIAL_VALUES:
        return True
    if (normalized.startswith("<") and normalized.endswith(">")) or (
        normalized.startswith("[") and normalized.endswith("]")
    ):
        return True
    if normalized.startswith(("${", "$")):
        return True
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value.strip()))


def _credential_value_present(value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return False
    if isinstance(value, str):
        return not _is_redacted_credential(value)
    if isinstance(value, (dict, list, tuple, set)):
        return bool(value)
    return True


def _string_contains_credential(value: str) -> bool:
    if DSN_RE.search(value) or URI_USERINFO_RE.search(value):
        return True
    for pattern in (QUERY_SECRET_RE, SECRET_ASSIGNMENT_RE, AUTH_HEADER_RE):
        for match in pattern.finditer(value):
            if not _is_redacted_credential(match.group(1)):
                return True
    return False


def _content_findings(payload: Any) -> list[dict[str, str]]:
    findings: set[tuple[str, str]] = set()

    def walk(value: Any, field: str) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                child = _field_path(field, key)
                if LOCAL_ABSOLUTE_PATH_RE.search(str(key)):
                    findings.add(("local_absolute_path", child))
                if _is_sensitive_key(str(key)) and _credential_value_present(item):
                    findings.add(("credential_value", child))
                walk(item, child)
            return
        if isinstance(value, list):
            for item in value:
                walk(item, f"{field}[]")
            return
        if not isinstance(value, str):
            return
        if LOCAL_ABSOLUTE_PATH_RE.search(value):
            findings.add(("local_absolute_path", field))
        if _string_contains_credential(value):
            findings.add(("credential_value", field))

    walk(payload, "$")
    return [
        {"code": code, "field": field}
        for code, field in sorted(findings, key=lambda item: (item[0], item[1]))
    ]


def _metadata_findings(payload: dict[str, Any]) -> list[dict[str, str]]:
    findings: list[dict[str, str]] = []
    for field in REQUIRED_FIELDS:
        if field not in payload:
            findings.append({"code": "missing_required_field", "field": f"$.{field}"})
    for field in sorted(STRING_FIELDS & payload.keys()):
        value = payload[field]
        if not isinstance(value, str):
            findings.append({"code": "invalid_field_type", "field": f"$.{field}"})
        elif not value.strip():
            findings.append({"code": "empty_required_field", "field": f"$.{field}"})
    if "worktree_dirty" in payload and not isinstance(payload["worktree_dirty"], bool):
        findings.append({"code": "invalid_field_type", "field": "$.worktree_dirty"})
    if "duration_seconds" in payload:
        duration = payload["duration_seconds"]
        if (
            isinstance(duration, bool)
            or not isinstance(duration, (int, float))
            or not math.isfinite(duration)
            or duration < 0
        ):
            findings.append({"code": "invalid_field_type", "field": "$.duration_seconds"})
    if "failures" in payload and not isinstance(payload["failures"], list):
        findings.append({"code": "invalid_field_type", "field": "$.failures"})
    if "artifact_checksums" in payload and not isinstance(payload["artifact_checksums"], dict):
        findings.append({"code": "invalid_field_type", "field": "$.artifact_checksums"})
    return findings


def _is_canonical_repo_relative(key: str) -> bool:
    if not key or "\\" in key or key.startswith("/"):
        return False
    path = PurePosixPath(key)
    return path.as_posix() == key and all(part not in {"", ".", ".."} for part in path.parts)


def _verify_artifact_checksums(
    checksums: Any,
    *,
    repo_root: Path,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    findings: list[dict[str, str]] = []
    verifications: list[dict[str, str]] = []
    if not isinstance(checksums, dict):
        return findings, verifications

    resolved_root = repo_root.resolve()
    for raw_key, expected in sorted(checksums.items(), key=lambda item: str(item[0])):
        if not isinstance(raw_key, str):
            label = "<non-string-artifact-key>"
            findings.append({"code": "invalid_artifact_key", "artifact": label})
            verifications.append({"artifact": label, "status": "invalid"})
            continue
        label = _safe_artifact_label(raw_key)
        if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
            findings.append({"code": "invalid_sha256", "artifact": label})
            verifications.append({"artifact": label, "status": "invalid"})
            continue
        if raw_key.startswith("<external>/"):
            suffix = raw_key.removeprefix("<external>/")
            if not _is_canonical_repo_relative(suffix):
                findings.append({"code": "invalid_external_artifact_key", "artifact": label})
                verifications.append({"artifact": label, "status": "invalid"})
                continue
            findings.append({"code": "external_artifact_unverifiable", "artifact": label})
            verifications.append({"artifact": label, "status": "unverifiable"})
            continue
        if not _is_canonical_repo_relative(raw_key):
            findings.append({"code": "artifact_path_not_repo_relative", "artifact": label})
            verifications.append({"artifact": label, "status": "invalid"})
            continue
        requested = resolved_root.joinpath(*PurePosixPath(raw_key).parts)
        try:
            resolved = requested.resolve(strict=True)
        except (OSError, RuntimeError):
            findings.append({"code": "artifact_missing", "artifact": label})
            verifications.append({"artifact": label, "status": "missing"})
            continue
        try:
            resolved.relative_to(resolved_root)
        except ValueError:
            findings.append({"code": "artifact_path_outside_repo", "artifact": label})
            verifications.append({"artifact": label, "status": "invalid"})
            continue
        if not resolved.is_file():
            findings.append({"code": "artifact_not_file", "artifact": label})
            verifications.append({"artifact": label, "status": "invalid"})
            continue
        try:
            actual = sha256_file(resolved)
        except OSError:
            findings.append({"code": "artifact_unreadable", "artifact": label})
            verifications.append({"artifact": label, "status": "unverifiable"})
            continue
        if actual != expected:
            findings.append({"code": "artifact_checksum_mismatch", "artifact": label})
            verifications.append({"artifact": label, "status": "mismatch"})
            continue
        verifications.append({"artifact": label, "status": "verified"})
    return findings, verifications


def verify_report(path: Path, *, repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    display = _display_path(path, repo_root)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return {
            "report": display,
            "result": "fail",
            "failures": [{"code": "report_unreadable", "field": "$"}],
            "artifact_verifications": [],
        }
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {
            "report": display,
            "result": "fail",
            "failures": [{"code": "invalid_json", "field": "$"}],
            "artifact_verifications": [],
        }
    if not isinstance(payload, dict):
        return {
            "report": display,
            "result": "fail",
            "failures": [{"code": "report_not_object", "field": "$"}],
            "artifact_verifications": [],
        }

    findings = _metadata_findings(payload)
    findings.extend(_content_findings(payload))
    checksum_findings, verifications = _verify_artifact_checksums(
        payload.get("artifact_checksums"), repo_root=repo_root
    )
    findings.extend(checksum_findings)
    findings.sort(
        key=lambda item: (
            item["code"],
            item.get("field", ""),
            item.get("artifact", ""),
        )
    )
    return {
        "report": display,
        "result": "pass" if not findings else "fail",
        "failures": findings,
        "artifact_verifications": verifications,
    }


def verify_reports(
    report_paths: Iterable[Path],
    *,
    repo_root: Path = REPO_ROOT,
    started_at: float | None = None,
) -> dict[str, Any]:
    started = time.monotonic() if started_at is None else started_at
    unique_paths = sorted(
        {Path(path).resolve() for path in report_paths},
        key=lambda path: _display_path(path, repo_root),
    )
    reports = [verify_report(path, repo_root=repo_root) for path in unique_paths]
    failed = [report for report in reports if report["result"] != "pass"]
    artifact_statuses = [
        artifact["status"]
        for report in reports
        for artifact in report["artifact_verifications"]
    ]
    failures = [
        {
            "code": "evidence_report_failed",
            "report": report["report"],
            "finding_count": len(report["failures"]),
        }
        for report in failed
    ]
    domain_report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "passed": not failed and bool(reports),
        "report_count": len(reports),
        "summary": {
            "pass": len(reports) - len(failed),
            "fail": len(failed),
            "findings": sum(len(report["failures"]) for report in reports),
            "artifact_verified": artifact_statuses.count("verified"),
            "artifact_unverifiable": artifact_statuses.count("unverifiable"),
            "artifact_mismatch": artifact_statuses.count("mismatch"),
        },
        "reports": reports,
    }
    return attach_evidence_metadata(
        domain_report,
        repo_root=repo_root,
        task_id="T12",
        environment_profile="local-read-only-evidence-verification",
        command=(
            "python scripts/maintenance/verify_optimization_evidence.py "
            "--report <configured-report> --json-output <artifact.json> "
            "--markdown-output <artifact.md>"
        ),
        result="pass" if domain_report["passed"] else "fail",
        failures=failures or ([{"code": "no_reports"}] if not reports else []),
        started_at=started,
        artifacts=unique_paths,
    )


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Optimization Evidence Verification",
        "",
        f"Status: **{'PASS' if report.get('passed') else 'FAIL'}**",
        "",
        f"- Generated: `{report.get('generated_at')}`",
        f"- Base commit: `{report.get('base_commit')}`",
        f"- Worktree dirty: `{report.get('worktree_dirty')}`",
        f"- Task: `{report.get('task_id')}`",
        f"- Environment: `{report.get('environment_profile')}`",
        f"- Result: `{report.get('result')}`",
        f"- Command: `{report.get('command')}`",
        f"- Duration: `{report.get('duration_seconds', 0):.3f}s`",
        f"- Reports: {report.get('report_count', 0)}",
        "",
        "| Report | Result | Findings | Verified | Unverifiable |",
        "| --- | --- | ---: | ---: | ---: |",
    ]
    for item in report.get("reports") or []:
        statuses = [artifact["status"] for artifact in item.get("artifact_verifications") or []]
        report_label = json.dumps(item.get("report", "<unknown>"), ensure_ascii=True)
        lines.append(
            f"| `{report_label}` | {item.get('result')} | {len(item.get('failures') or [])} | "
            f"{statuses.count('verified')} | {statuses.count('unverifiable')} |"
        )
        for failure in item.get("failures") or []:
            detail = json.dumps(failure, ensure_ascii=True, sort_keys=True)
            lines.append(f"| finding | `{detail}` |  |  |  |")
    if not report.get("reports"):
        lines.append("| none | fail | 1 | 0 | 0 |")
    lines.extend(["", "## Aggregate Failures", ""])
    aggregate_failures = report.get("failures") or []
    lines.extend(
        [
            f"- `{json.dumps(failure, ensure_ascii=True, sort_keys=True)}`"
            for failure in aggregate_failures
        ]
        or ["- None"]
    )
    lines.extend(
        [
            "",
            "## Input Artifact Checksums",
            "",
            "| Artifact | SHA-256 |",
            "| --- | --- |",
        ]
    )
    for artifact, checksum in sorted((report.get("artifact_checksums") or {}).items()):
        lines.append(
            f"| `{_safe_artifact_label(str(artifact))}` | `{checksum}` |"
        )
    return "\n".join(lines) + "\n"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, action="append", required=True)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--markdown-output", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    input_paths = {path.resolve() for path in args.report}
    output_paths = {
        path.resolve() for path in (args.json_output, args.markdown_output) if path is not None
    }
    if input_paths & output_paths or (
        args.json_output is not None
        and args.markdown_output is not None
        and args.json_output.resolve() == args.markdown_output.resolve()
    ):
        print("FAIL optimization evidence verification: unsafe output path configuration")
        return 2

    report = verify_reports(args.report)
    if args.json_output is not None:
        _write_text(
            args.json_output,
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
    if args.markdown_output is not None:
        _write_text(args.markdown_output, render_markdown(report))
    print(
        f"{'PASS' if report['passed'] else 'FAIL'} optimization evidence verification: "
        f"reports={report['report_count']} findings={report['summary']['findings']}"
    )
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
