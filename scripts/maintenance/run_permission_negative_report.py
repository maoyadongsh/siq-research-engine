#!/usr/bin/env python3
"""Run the release permission matrix and write a fail-closed report.

The runner deliberately executes existing API pytest nodes one at a time.  It
does not call application code itself, so the evidence remains tied to the
real FastAPI/TestClient (or route-level) authorization tests.  Only stable
case metadata and a short, redacted failure summary are persisted.
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / "apps" / "api"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "optimization" / "2026-07-12" / "release" / "permission-negative-report.json"
DEFAULT_MARKDOWN = REPO_ROOT / "artifacts" / "optimization" / "2026-07-12" / "release" / "permission-negative-report.md"
SCHEMA_VERSION = "siq_permission_negative_report_v1"
REQUIRED_SURFACES = {
    "deal",
    "history",
    "audit",
    "artifact",
    "source",
    "task_list",
    "task_read",
    "task_write",
}
REQUIRED_ROLES = {"owner", "other", "admin"}


@dataclass(frozen=True)
class PermissionCase:
    case_id: str
    surface: str
    roles: tuple[str, ...]
    expected: str
    node_id: str


# Keep this manifest explicit.  A missing node is a release failure rather than
# silently reducing the matrix to whatever pytest happens to discover.
CASES: tuple[PermissionCase, ...] = (
    PermissionCase(
        "deal-owner-other-object",
        "deal",
        ("owner", "other"),
        "owner can read; other cannot read, list, or upload the private deal",
        "tests/test_deals_router.py::test_deals_router_private_deal_requires_object_access",
    ),
    PermissionCase(
        "deal-admin-baseline",
        "deal",
        ("admin",),
        "admin can create, list, and read a deal object",
        "tests/test_deals_router.py::test_deals_router_create_list_and_detail",
    ),
    PermissionCase(
        "history-owner",
        "history",
        ("owner",),
        "owner can read the current user's assistant history contract",
        "tests/test_chat_route_usage.py::test_chat_answer_audit_trace_route_requires_current_user_session",
    ),
    PermissionCase(
        "history-other-denied",
        "history",
        ("other",),
        "other user receives a not-found response for another user's history trace",
        "tests/test_chat_route_usage.py::test_chat_answer_audit_trace_route_hides_other_users_trace",
    ),
    PermissionCase(
        "audit-owner-other",
        "audit",
        ("owner", "other"),
        "owner can read an audit trace; another user cannot",
        "tests/test_router_history_response.py::test_specialist_answer_audit_trace_route_uses_profile_session_ownership",
    ),
    PermissionCase(
        "artifact-owner-other-read-write",
        "artifact",
        ("owner", "other"),
        "owner can read/write an owned parse artifact; other cannot cross the object boundary",
        "tests/test_workflow_subprocess_contracts.py::test_workflow_pdf_task_routes_require_user_artifact_and_write_permission",
    ),
    PermissionCase(
        "artifact-admin-read",
        "artifact",
        ("admin",),
        "admin can access an arbitrary document task",
        "tests/test_document_parser_proxy.py::test_admin_can_access_any_document_task",
    ),
    PermissionCase(
        "source-owner",
        "source",
        ("owner",),
        "owner receives a signed source URL and upstream is reachable",
        "tests/test_source_access.py::test_source_access_route_mints_clean_signed_url_for_owner",
    ),
    PermissionCase(
        "source-other-denied",
        "source",
        ("other",),
        "other user receives 403 and no upstream source proxy call",
        "tests/test_source_access.py::test_source_access_route_rejects_non_owner_without_upstream_proxy",
    ),
    PermissionCase(
        "source-admin-access-rule",
        "source",
        ("admin",),
        "admin bypasses task workspace ownership in the shared source access rule",
        "tests/test_source_access.py::test_user_has_task_access_allows_admin_without_parse_artifact",
    ),
    PermissionCase(
        "task-list-other-workspace",
        "task_list",
        ("other",),
        "non-admin task list is restricted to the current user's workspace",
        "tests/test_workspace_sync.py::test_list_my_pdf_tasks_defaults_to_workspace_scope_for_non_admin",
    ),
    PermissionCase(
        "task-list-admin-system",
        "task_list",
        ("admin",),
        "admin task list uses system scope when workspace-only is not enabled",
        "tests/test_document_parser_proxy.py::test_list_document_tasks_admin_defaults_to_system_scope",
    ),
    PermissionCase(
        "task-read-write-owner-other",
        "task_read",
        ("owner", "other"),
        "owner can read; other cannot read or trigger task writes",
        "tests/test_workflow_auth.py::test_workflow_http_read_and_write_are_bound_to_parse_task_owner",
    ),
    PermissionCase(
        "task-write-owner-other",
        "task_write",
        ("owner", "other"),
        "owner write path is available; other user write paths are rejected before upstream execution",
        "tests/test_workflow_subprocess_contracts.py::test_workflow_pdf_task_routes_require_user_artifact_and_write_permission",
    ),
    PermissionCase(
        "task-write-admin",
        "task_write",
        ("admin",),
        "admin can start the remaining workflow and read its job status",
        "tests/test_workflow_subprocess_contracts.py::test_workflow_run_remaining_and_job_status_do_not_cross_users",
    ),
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def validate_manifest(cases: Sequence[PermissionCase] = CASES) -> None:
    """Reject accidental matrix erosion before any subprocess is started."""
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate_permission_case_id")
    missing_fields = [case.case_id for case in cases if not case.node_id or not case.surface or not case.expected]
    if missing_fields:
        raise ValueError("manifest_case_missing_required_field")
    surfaces = {case.surface for case in cases}
    roles = {role for case in cases for role in case.roles}
    missing_surfaces = sorted(REQUIRED_SURFACES - surfaces)
    missing_roles = sorted(REQUIRED_ROLES - roles)
    if missing_surfaces:
        raise ValueError(f"manifest_missing_surfaces:{','.join(missing_surfaces)}")
    if missing_roles:
        raise ValueError(f"manifest_missing_roles:{','.join(missing_roles)}")


def redact_failure(value: str, *, limit: int = 280) -> str:
    """Keep only a short diagnostic; never persist credentials or object data."""
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", str(value or ""))
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"(?i)(authorization|access_token|source_token|api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
    text = re.sub(r"https?://[^\s,;]+", "[url-redacted]", text)
    text = re.sub(r"/[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", "[path-redacted]", text)
    text = re.sub(r"(?i)([A-Za-z0-9_.+-]+@[A-Za-z0-9.-]+)", "[email-redacted]", text)
    text = re.sub(r"(?i)(deal|task|trace|session|user|artifact)[_-]?[A-Za-z0-9-]+", r"\1-[id-redacted]", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def _default_pytest_command() -> list[str]:
    override = os.environ.get("SIQ_PERMISSION_PYTEST_COMMAND", "").strip()
    return shlex.split(override) if override else ["uv", "run", "--frozen", "pytest"]


def _failure_summary(stdout: str, stderr: str, returncode: int) -> str:
    """Return a category only; pytest assertion output can contain object data."""
    combined = "\n".join(part for part in (stdout, stderr) if part)
    if re.search(r"no tests collected|file or directory not found", combined, flags=re.IGNORECASE):
        category = "pytest_node_not_found_or_empty"
    elif re.search(r"error", combined, flags=re.IGNORECASE):
        category = "pytest_error"
    elif "failed" in combined.lower():
        category = "pytest_failed"
    else:
        category = "pytest_no_safe_summary"
    return f"exit_code={returncode}; {category}"


def run_case(case: PermissionCase, *, pytest_command: Sequence[str] | None = None, timeout: float = 300.0, runner: Any = subprocess.run) -> dict[str, Any]:
    command = list(pytest_command or _default_pytest_command()) + ["-q", "--disable-warnings", "--maxfail=1", case.node_id]
    started = time.monotonic()
    try:
        completed = runner(
            command,
            cwd=API_ROOT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        returncode = int(getattr(completed, "returncode", 1))
        stdout = str(getattr(completed, "stdout", "") or "")
        stderr = str(getattr(completed, "stderr", "") or "")
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        stdout = str(getattr(exc, "stdout", "") or "")
        stderr = "pytest_timeout"
    except Exception as exc:  # pragma: no cover - defensive runner boundary
        returncode = 1
        stdout = ""
        stderr = f"runner_error:{type(exc).__name__}"
    duration = round(time.monotonic() - started, 3)
    combined = f"{stdout}\n{stderr}"
    skipped = bool(re.search(r"\b(?:1|[2-9]\d*) skipped\b", combined))
    collected_zero = bool(re.search(r"no tests collected", combined, flags=re.IGNORECASE))
    passed = returncode == 0 and not skipped and not collected_zero
    status = "passed" if passed else ("skipped" if skipped else "failed")
    result: dict[str, Any] = {
        "case_id": case.case_id,
        "surface": case.surface,
        "roles": list(case.roles),
        "expected": case.expected,
        "node_id": case.node_id,
        "status": status,
        "passed": passed,
        "duration_seconds": duration,
    }
    if not passed:
        result["failure_summary"] = "pytest_skipped" if skipped else _failure_summary(stdout, stderr, returncode)
    return result


def run_report(*, cases: Sequence[PermissionCase] = CASES, pytest_command: Sequence[str] | None = None, timeout: float = 300.0, runner: Any = subprocess.run) -> dict[str, Any]:
    validate_manifest(cases)
    results = [run_case(case, pytest_command=pytest_command, timeout=timeout, runner=runner) for case in cases]
    passed_count = sum(1 for result in results if result["passed"])
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now_iso(),
        "status": "passed" if passed_count == len(results) else "failed",
        "passed": passed_count == len(results),
        "summary": {"cases": len(results), "passed_cases": passed_count, "failed_cases": len(results) - passed_count},
        "results": results,
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    lines = [
        "# 双用户对象权限负向报告",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Cases: `{summary.get('passed_cases', 0)}/{summary.get('cases', 0)}`",
        "- Policy: any missing, skipped, or failed node fails closed",
        "",
        "| Case | Surface | Roles | Expected | Status | Duration (s) | Failure summary |",
        "| --- | --- | --- | --- | --- | ---: | --- |",
    ]
    for item in report.get("results", []):
        if not isinstance(item, Mapping):
            continue
        failure = str(item.get("failure_summary") or "").replace("|", "\\|")
        expected = str(item.get("expected") or "").replace("|", "\\|")
        lines.append(
            f"| `{item.get('case_id')}` | `{item.get('surface')}` | `{','.join(item.get('roles') or [])}` | {expected} | `{item.get('status')}` | {item.get('duration_seconds', 0)} | {failure} |"
        )
    return "\n".join(lines) + "\n"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--pytest-command", default="", help="Override pytest command, parsed with shell-like quoting.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    command = shlex.split(args.pytest_command) if args.pytest_command else None
    try:
        report = run_report(cases=CASES, pytest_command=command, timeout=args.timeout)
    except ValueError as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "generated_at": now_iso(),
            "status": "blocked",
            "passed": False,
            "summary": {"cases": 0, "passed_cases": 0, "failed_cases": 0},
            "failure_summary": redact_failure(str(exc)),
            "results": [],
        }
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    markdown = args.markdown if args.markdown.is_absolute() else REPO_ROOT / args.markdown
    output.parent.mkdir(parents=True, exist_ok=True)
    markdown.parent.mkdir(parents=True, exist_ok=True)
    import json

    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "passed": report["passed"], "cases": report["summary"]["cases"]}, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
