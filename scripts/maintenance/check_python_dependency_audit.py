#!/usr/bin/env python3
"""Audit production Python lock files with pip-audit."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TARGETS = (
    ("api", Path("apps/api")),
    ("market-report-finder", Path("services/market-report-finder")),
    ("market-report-rules", Path("services/market-report-rules")),
)
BLOCKING_SEVERITIES = {"high", "critical"}
PIP_AUDIT_UVX_SPEC = "pip-audit==2.10.1"


@dataclass(frozen=True)
class DependencyAuditTarget:
    name: str
    workdir: str


@dataclass(frozen=True)
class DependencyVulnerability:
    target: str
    package: str
    version: str | None
    vulnerability_id: str
    aliases: list[str]
    fix_versions: list[str]
    severity: str | None
    description: str


@dataclass
class TargetAuditResult:
    name: str
    workdir: str
    status: str
    requirements_file: str | None = None
    export_command: list[str] = field(default_factory=list)
    audit_command: list[str] = field(default_factory=list)
    vulnerability_count: int = 0
    blocking_vulnerability_count: int = 0
    vulnerabilities: list[DependencyVulnerability] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    messages: list[str] = field(default_factory=list)


@dataclass
class DependencyAuditResult:
    status: str
    targets: list[TargetAuditResult]
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _target_by_name() -> dict[str, DependencyAuditTarget]:
    return {
        name: DependencyAuditTarget(name=name, workdir=workdir.as_posix())
        for name, workdir in DEFAULT_TARGETS
    }


def _selected_targets(selected: list[str] | None) -> list[DependencyAuditTarget]:
    by_name = _target_by_name()
    if not selected:
        return list(by_name.values())
    missing = [name for name in selected if name not in by_name]
    if missing:
        raise ValueError(f"unknown Python dependency audit target(s): {', '.join(missing)}")
    return [by_name[name] for name in selected]


def _run_command(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def _short_description(value: Any, *, limit: int = 240) -> str:
    text = str(value or "").replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}…"


def _severity(vuln: dict[str, Any]) -> str | None:
    value = vuln.get("severity")
    if value is None:
        value = vuln.get("cvss_severity")
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _is_blocking(vuln: dict[str, Any], *, block_all_vulnerabilities: bool) -> bool:
    if block_all_vulnerabilities:
        return True
    severity = _severity(vuln)
    return severity in BLOCKING_SEVERITIES


def _vulnerability(target: str, dependency: dict[str, Any], vuln: dict[str, Any]) -> DependencyVulnerability:
    aliases = [str(item) for item in vuln.get("aliases") or [] if str(item).strip()]
    fix_versions = [str(item) for item in vuln.get("fix_versions") or [] if str(item).strip()]
    return DependencyVulnerability(
        target=target,
        package=str(dependency.get("name") or ""),
        version=str(dependency.get("version")) if dependency.get("version") is not None else None,
        vulnerability_id=str(vuln.get("id") or ""),
        aliases=aliases,
        fix_versions=fix_versions,
        severity=_severity(vuln),
        description=_short_description(vuln.get("description")),
    )


def _parse_audit_report(
    target: str,
    payload: dict[str, Any],
    *,
    block_all_vulnerabilities: bool,
) -> tuple[list[DependencyVulnerability], list[DependencyVulnerability]]:
    vulnerabilities: list[DependencyVulnerability] = []
    blocking: list[DependencyVulnerability] = []
    for dependency in payload.get("dependencies") or []:
        if not isinstance(dependency, dict):
            continue
        for vuln in dependency.get("vulns") or []:
            if not isinstance(vuln, dict):
                continue
            record = _vulnerability(target, dependency, vuln)
            vulnerabilities.append(record)
            if _is_blocking(vuln, block_all_vulnerabilities=block_all_vulnerabilities):
                blocking.append(record)
    return vulnerabilities, blocking


def audit_target(
    repo_root: Path,
    target: DependencyAuditTarget,
    *,
    output_dir: Path,
    uv_executable: str,
    pip_audit_command: list[str],
    block_all_vulnerabilities: bool,
) -> TargetAuditResult:
    workdir = repo_root / target.workdir
    result = TargetAuditResult(name=target.name, workdir=target.workdir, status="pending")
    if not workdir.is_dir():
        result.status = "failed"
        result.messages.append(f"target workdir does not exist: {target.workdir}")
        return result

    requirements_path = output_dir / f"{target.name}-requirements.txt"
    report_path = output_dir / f"{target.name}-pip-audit.json"
    export_command = [
        uv_executable,
        "export",
        "--locked",
        "--no-dev",
        "--no-emit-project",
        "--no-emit-workspace",
        "--no-emit-local",
        "--no-hashes",
        "--output-file",
        str(requirements_path),
    ]
    result.export_command = export_command
    export_completed = _run_command(export_command, cwd=workdir)
    if export_completed.returncode != 0:
        result.stdout = export_completed.stdout
        result.stderr = export_completed.stderr
        result.status = "failed"
        result.messages.append("uv export failed")
        return result

    result.requirements_file = requirements_path.as_posix()
    audit_command = [
        *pip_audit_command,
        "-r",
        str(requirements_path),
        "--format",
        "json",
        "--progress-spinner",
        "off",
    ]
    result.audit_command = audit_command
    audit_completed = _run_command(audit_command, cwd=workdir)
    result.stderr = audit_completed.stderr.strip()

    if audit_completed.stdout.strip():
        report_path.write_text(audit_completed.stdout, encoding="utf-8")
    try:
        payload = json.loads(audit_completed.stdout or "{}")
    except json.JSONDecodeError:
        result.stdout = _short_description(audit_completed.stdout, limit=1000)
        result.status = "failed"
        result.messages.append("pip-audit did not emit valid JSON")
        return result

    vulnerabilities, blocking = _parse_audit_report(
        target.name,
        payload,
        block_all_vulnerabilities=block_all_vulnerabilities,
    )
    result.vulnerabilities = vulnerabilities
    result.vulnerability_count = len(vulnerabilities)
    result.blocking_vulnerability_count = len(blocking)
    if blocking:
        result.status = "failed"
        result.messages.append(
            f"found {len(blocking)} blocking vulnerability finding(s) in {target.name}"
        )
    elif audit_completed.returncode not in (0, 1):
        result.status = "failed"
        result.messages.append(f"pip-audit failed with return code {audit_completed.returncode}")
    else:
        result.status = "passed"
        if vulnerabilities:
            result.messages.append(
                f"found {len(vulnerabilities)} non-blocking vulnerability finding(s) in {target.name}"
            )
    return result


def run_dependency_audit(
    repo_root: Path,
    *,
    targets: list[str] | None = None,
    output_dir: Path | None = None,
    uv_executable: str = "uv",
    pip_audit_executable: str = "pip-audit",
    require_pip_audit: bool = False,
    block_all_vulnerabilities: bool = False,
) -> tuple[int, DependencyAuditResult]:
    selected_targets = _selected_targets(targets)
    messages: list[str] = []
    uv_path = shutil.which(uv_executable) or uv_executable
    pip_audit_path = shutil.which(pip_audit_executable)
    if pip_audit_path is None:
        uvx_path = shutil.which("uvx")
        if pip_audit_executable == "pip-audit" and uvx_path:
            pip_audit_command = [uvx_path, "--from", PIP_AUDIT_UVX_SPEC, "pip-audit"]
            messages.append(f"pip-audit executable not found; using uvx fallback {PIP_AUDIT_UVX_SPEC}")
        else:
            message = f"pip-audit executable not found: {pip_audit_executable}"
            messages.append(message)
            status = "failed" if require_pip_audit else "advisory"
            return (
                1 if require_pip_audit else 0,
                DependencyAuditResult(status=status, targets=[], messages=messages),
            )
    else:
        pip_audit_command = [pip_audit_path]

    if output_dir is None:
        with tempfile.TemporaryDirectory(prefix="siq-python-dependency-audit-") as temp_dir:
            return _run_dependency_audit_with_output_dir(
                repo_root,
                selected_targets=selected_targets,
                output_dir=Path(temp_dir),
                uv_path=uv_path,
                pip_audit_command=pip_audit_command,
                block_all_vulnerabilities=block_all_vulnerabilities,
                messages=messages,
            )
    return _run_dependency_audit_with_output_dir(
        repo_root,
        selected_targets=selected_targets,
        output_dir=output_dir,
        uv_path=uv_path,
        pip_audit_command=pip_audit_command,
        block_all_vulnerabilities=block_all_vulnerabilities,
        messages=messages,
    )


def _run_dependency_audit_with_output_dir(
    repo_root: Path,
    *,
    selected_targets: list[DependencyAuditTarget],
    output_dir: Path,
    uv_path: str,
    pip_audit_command: list[str],
    block_all_vulnerabilities: bool,
    messages: list[str],
) -> tuple[int, DependencyAuditResult]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results = [
        audit_target(
            repo_root,
            target,
            output_dir=output_dir,
            uv_executable=uv_path,
            pip_audit_command=pip_audit_command,
            block_all_vulnerabilities=block_all_vulnerabilities,
        )
        for target in selected_targets
    ]
    failed = [result for result in results if result.status == "failed"]
    status = "failed" if failed else "passed"
    if failed:
        messages.append(f"{len(failed)} Python dependency audit target(s) failed")
    else:
        messages.append("All Python dependency audit targets passed")
    return (1 if failed else 0), DependencyAuditResult(status=status, targets=results, messages=messages)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit SIQ Python production dependency lock files.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--uv-executable", default="uv")
    parser.add_argument("--pip-audit-executable", default="pip-audit")
    parser.add_argument("--require-pip-audit", action="store_true")
    parser.add_argument(
        "--block-all-vulnerabilities",
        action="store_true",
        help="Treat every pip-audit finding as blocking, including findings without severity metadata.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        exit_code, result = run_dependency_audit(
            args.repo_root.resolve(),
            targets=args.targets,
            output_dir=args.output_dir.resolve() if args.output_dir else None,
            uv_executable=args.uv_executable,
            pip_audit_executable=args.pip_audit_executable,
            require_pip_audit=args.require_pip_audit,
            block_all_vulnerabilities=args.block_all_vulnerabilities,
        )
    except ValueError as exc:
        result = DependencyAuditResult(status="failed", targets=[], messages=[str(exc)])
        exit_code = 2

    if args.json_output:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Python dependency audit status: {result.status}")
        for message in result.messages:
            print(message, file=sys.stderr if exit_code else sys.stdout)
        for target in result.targets:
            print(
                f"- {target.name}: {target.status} "
                f"({target.blocking_vulnerability_count} blocking / {target.vulnerability_count} total)"
            )
            for message in target.messages:
                print(f"  {message}", file=sys.stderr if target.status == "failed" else sys.stdout)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
