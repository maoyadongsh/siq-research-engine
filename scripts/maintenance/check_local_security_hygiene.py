#!/usr/bin/env python3
"""Check local-only security hygiene without changing files."""

from __future__ import annotations

import argparse
import json
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SENSITIVE_LOCAL_DIRS = ("data", "artifacts", "runtime", "runtimes", "var")
SENSITIVE_LOCAL_FILE_SCAN_MAX_DEPTH = 4
SENSITIVE_LOCAL_FILE_FINDING_LIMIT = 50
POSTGRES_RELEASE_GATE_WORKFLOW = Path(".github/workflows/market-postgres-release-gate.yml")
PRODUCTION_STARTUP_GUARD_FILES = (Path("start_all.sh"), Path("apps/api/start.sh"))
CI_WORKFLOW = Path(".github/workflows/ci.yml")
COMPOSE_FILE = Path("infra/docker/docker-compose.yml")
MILVUS_COMPOSE_FILE = Path("infra/vector-index/milvus/docker-compose.yml")
API_START_SCRIPT = Path("apps/api/start.sh")
SUPERVISOR_CONFIG = Path("infra/supervisor/supervisord.conf")
GITIGNORE_FILE = Path(".gitignore")
REQUIRED_RUNTIME_IGNORE_PATTERNS = (
    "data/**",
    "data/wiki/",
    "data/postgres/",
    "data/pdf-parser/results/",
    "var/**",
    "artifacts/**",
    "env/*.env",
    "infra/env/*.env",
)
SERVICE_DOCKERFILES = (
    Path("apps/api/Dockerfile"),
    Path("apps/web/Dockerfile"),
    Path("apps/pdf-parser/Dockerfile"),
    Path("apps/document-parser/Dockerfile"),
    Path("services/market-report-finder/Dockerfile"),
    Path("services/market-report-rules/Dockerfile"),
)
COMPOSE_SERVICE_USERS = (
    "web",
    "api",
    "report-finder",
    "market-report-finder",
    "market-report-rules",
    "pdf-parser",
    "document-parser",
)
CHECK_SCOPES = ("all", "local-dirs", "workflow")

TRUST_AUTH_PATTERNS = (
    re.compile(r"\bPOSTGRES_HOST_AUTH_METHOD\b\s*[:=]\s*['\"]?trust['\"]?(?:\s|$)", re.IGNORECASE),
    re.compile(r"\b--auth(?:-host|-local)?=trust\b", re.IGNORECASE),
    re.compile(r"^\s*(?:host|local)\s+\S+\s+\S+(?:\s+\S+){0,3}\s+trust\s*$", re.IGNORECASE),
)
LOOPBACK_5432_BINDINGS = (
    "127.0.0.1:5432:5432",
    "localhost:5432:5432",
    "[::1]:5432:5432",
)
BINDING_TOKEN_PATTERN = re.compile(r"['\"]?([^\s,'\"]+:\d+(?::\d+)?)['\"]?")
SERVICE_BLOCK_PATTERN = re.compile(r"(?ms)^  (?P<service>[A-Za-z0-9_-]+):\n(?P<body>.*?)(?=^  [A-Za-z0-9_-]+:|\Z)")
USER_INSTRUCTION_PATTERN = re.compile(r"(?im)^\s*USER\s+(.+?)\s*$")
SUPERVISOR_SECTION_PATTERN = re.compile(r"(?ms)^\[(?P<section>[^\]]+)\]\n(?P<body>.*?)(?=^\[[^\]]+\]|\Z)")
MINIO_DEFAULT_CREDENTIAL_PATTERN = re.compile(
    r"\bMINIO_(?:ACCESS_KEY|SECRET_KEY|ROOT_USER|ROOT_PASSWORD)\b.*(?:minioadmin|\$\{[^}:]+:-[^}]*minioadmin)",
    re.IGNORECASE,
)
MILVUS_PUBLIC_CONTAINER_PORTS = {"8000", "9000", "9001", "9091", "19530"}
DATABASE_URL_LOG_STATUS_CALL = '$(database_url_log_status "$database_url_for_log")'
DATABASE_URL_DIRECT_LOG_PATTERN = re.compile(
    r"\b(?:echo|printf)\b.*(?:\$\{?(?:SIQ_APP_DATABASE_URL|DATABASE_URL)\b|\$database_url_for_log\b)"
)


@dataclass(frozen=True)
class HygieneFinding:
    code: str
    path: str
    detail: str
    line: int | None = None


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return path.as_posix()


def _relative_depth(path: Path, root: Path) -> int:
    try:
        return len(path.relative_to(root).parts)
    except ValueError:
        return 0


def _strip_comment(line: str) -> str:
    return line.split("#", 1)[0].strip()


def _normalize_ignore_pattern(value: str) -> str:
    text = value.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text


def _contains_trust_auth(line: str) -> bool:
    text = _strip_comment(line)
    return any(pattern.search(text) for pattern in TRUST_AUTH_PATTERNS)


def _contains_wide_5432_binding(line: str) -> bool:
    text = _strip_comment(line)
    for match in BINDING_TOKEN_PATTERN.finditer(text):
        token = match.group(1)
        if token == "5432:5432":
            return True
        if token in LOOPBACK_5432_BINDINGS:
            continue
        if token.startswith("0.0.0.0:5432:5432") or token.startswith("[::]:5432:5432"):
            return True
    return False


def _contains_wide_milvus_binding(line: str) -> bool:
    text = _strip_comment(line).strip()
    if not text.startswith("-"):
        return False
    value = text[1:].strip().strip("\"'")
    if value.startswith(("127.0.0.1:", "localhost:", "[::1]:")):
        return False
    if value.startswith(("0.0.0.0:", "[::]:")):
        return value.rsplit(":", 1)[-1] in MILVUS_PUBLIC_CONTAINER_PORTS
    if ":" not in value:
        return False
    return value.rsplit(":", 1)[-1] in MILVUS_PUBLIC_CONTAINER_PORTS


def check_sensitive_local_dirs(repo_root: Path) -> list[HygieneFinding]:
    findings: list[HygieneFinding] = []
    for name in SENSITIVE_LOCAL_DIRS:
        path = repo_root / name
        if not path.is_dir():
            continue
        mode = stat.S_IMODE(path.stat().st_mode)
        permissions: list[str] = []
        if mode & stat.S_IROTH:
            permissions.append("world-readable")
        if mode & stat.S_IXOTH:
            permissions.append("world-executable")
        if permissions:
            findings.append(
                HygieneFinding(
                    code="local_sensitive_dir_world_access",
                    path=_repo_relative(path, repo_root),
                    detail=f"{', '.join(permissions)} permissions set (mode {mode:04o})",
                )
            )
            continue
        for child in path.rglob("*"):
            if len(findings) >= SENSITIVE_LOCAL_FILE_FINDING_LIMIT:
                findings.append(
                    HygieneFinding(
                        code="local_sensitive_file_world_access_limit",
                        path=_repo_relative(path, repo_root),
                        detail=(
                            "Stopped after "
                            f"{SENSITIVE_LOCAL_FILE_FINDING_LIMIT} local file permission findings; "
                            "fix the listed paths and rerun for the full scan"
                        ),
                    )
                )
                return findings
            if child.is_symlink() or not child.is_file() or _relative_depth(child, path) > SENSITIVE_LOCAL_FILE_SCAN_MAX_DEPTH:
                continue
            child_mode = stat.S_IMODE(child.stat().st_mode)
            child_permissions: list[str] = []
            if child_mode & stat.S_IROTH:
                child_permissions.append("world-readable")
            if child_mode & stat.S_IWOTH:
                child_permissions.append("world-writable")
            if child_mode & stat.S_IXOTH:
                child_permissions.append("world-executable")
            if child_permissions:
                findings.append(
                    HygieneFinding(
                        code="local_sensitive_file_world_access",
                        path=_repo_relative(child, repo_root),
                        detail=f"{', '.join(child_permissions)} permissions set (mode {child_mode:04o})",
                    )
                )
    return findings


def check_postgres_release_gate_workflow(repo_root: Path) -> list[HygieneFinding]:
    workflow = repo_root / POSTGRES_RELEASE_GATE_WORKFLOW
    if not workflow.exists():
        return []

    findings: list[HygieneFinding] = []
    for line_number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), start=1):
        if _contains_trust_auth(line):
            findings.append(
                HygieneFinding(
                    code="postgres_trust_auth",
                    path=_repo_relative(workflow, repo_root),
                    detail="PostgreSQL trust authentication is configured",
                    line=line_number,
                )
            )
        if _contains_wide_5432_binding(line):
            findings.append(
                HygieneFinding(
                    code="postgres_wide_5432_binding",
                    path=_repo_relative(workflow, repo_root),
                    detail="PostgreSQL port 5432 is bound broadly as 5432:5432",
                    line=line_number,
                )
            )
    return findings


def check_milvus_compose_hygiene(repo_root: Path) -> list[HygieneFinding]:
    path = repo_root / MILVUS_COMPOSE_FILE
    if not path.exists():
        return []

    findings: list[HygieneFinding] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if MINIO_DEFAULT_CREDENTIAL_PATTERN.search(_strip_comment(line)):
            findings.append(
                HygieneFinding(
                    code="milvus_minio_default_credentials",
                    path=_repo_relative(path, repo_root),
                    detail="Milvus MinIO uses default or fallback minioadmin credentials",
                    line=line_number,
                )
            )
        if _contains_wide_milvus_binding(line):
            findings.append(
                HygieneFinding(
                    code="milvus_public_port_binding",
                    path=_repo_relative(path, repo_root),
                    detail="Milvus/MinIO/Attu port is published without an explicit loopback host binding",
                    line=line_number,
                )
            )
    return findings


def check_production_startup_guards(repo_root: Path) -> list[HygieneFinding]:
    findings: list[HygieneFinding] = []
    for relative_path in PRODUCTION_STARTUP_GUARD_FILES:
        path = repo_root / relative_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        missing: list[str] = []
        for token in (
            "SIQ_DEPLOYMENT_PROFILE",
            "SIQ_UVICORN_RELOAD must not be enabled",
            "FLASK_DEBUG must not be enabled",
            "uvicorn_args+=(--reload)",
        ):
            if token not in text:
                missing.append(token)
        if relative_path == API_START_SCRIPT:
            for token in (
                "database_url_log_status()",
                'printf \'%s\\n\' "configured"',
                'printf \'%s\\n\' "not configured"',
                DATABASE_URL_LOG_STATUS_CALL,
            ):
                if token not in text:
                    missing.append(token)
        if missing:
            findings.append(
                HygieneFinding(
                    code="production_startup_guard_missing",
                    path=_repo_relative(path, repo_root),
                    detail="Missing production startup guard token(s): " + ", ".join(missing),
                )
            )
        if "uv run python -m uvicorn main:app --host 0.0.0.0" in text or (
            "uv run python -m uvicorn main:app --reload --host 0.0.0.0" in text
        ):
            findings.append(
                HygieneFinding(
                    code="production_startup_hardcoded_dev_uvicorn",
                    path=_repo_relative(path, repo_root),
                    detail="Uvicorn startup is hardcoded to development host/reload flags",
                )
            )
        if relative_path == API_START_SCRIPT:
            for line_number, line in enumerate(text.splitlines(), start=1):
                stripped = _strip_comment(line)
                unsafe_partial_redaction = (
                    "redact_database_url_for_log" in stripped
                    or "***@" in stripped
                    or "password=***" in stripped.lower()
                )
                unsafe_direct_log = (
                    DATABASE_URL_DIRECT_LOG_PATTERN.search(stripped) is not None
                    and DATABASE_URL_LOG_STATUS_CALL not in stripped
                )
                if unsafe_partial_redaction or unsafe_direct_log:
                    findings.append(
                        HygieneFinding(
                            code="production_startup_database_url_log_unsafe",
                            path=_repo_relative(path, repo_root),
                            detail="Database URL logs must expose configured/not configured status only",
                            line=line_number,
                        )
                    )
    return findings


def _compose_service_blocks(compose_text: str) -> dict[str, str]:
    return {match.group("service"): match.group("body") for match in SERVICE_BLOCK_PATTERN.finditer(compose_text)}


def _is_root_user_instruction(value: str) -> bool:
    normalized = value.strip().strip("\"'").lower()
    return normalized in {"0", "0:0", "root", "root:root"}


def check_container_security_config(repo_root: Path) -> list[HygieneFinding]:
    findings: list[HygieneFinding] = []

    for relative_path in SERVICE_DOCKERFILES:
        path = repo_root / relative_path
        if not path.exists():
            findings.append(
                HygieneFinding(
                    code="service_dockerfile_missing",
                    path=relative_path.as_posix(),
                    detail="Expected service Dockerfile is missing",
                )
            )
            continue
        users = USER_INSTRUCTION_PATTERN.findall(path.read_text(encoding="utf-8"))
        if not users:
            findings.append(
                HygieneFinding(
                    code="service_dockerfile_missing_user",
                    path=_repo_relative(path, repo_root),
                    detail="Service Dockerfile does not declare a USER instruction",
                )
            )
        elif _is_root_user_instruction(users[-1]):
            findings.append(
                HygieneFinding(
                    code="service_dockerfile_root_user",
                    path=_repo_relative(path, repo_root),
                    detail=f"Service Dockerfile final USER instruction is root-like: {users[-1]}",
                )
            )

    compose = repo_root / COMPOSE_FILE
    if compose.exists():
        blocks = _compose_service_blocks(compose.read_text(encoding="utf-8"))
        for service in COMPOSE_SERVICE_USERS:
            body = blocks.get(service)
            if body is None:
                findings.append(
                    HygieneFinding(
                        code="compose_service_missing",
                        path=_repo_relative(compose, repo_root),
                        detail=f"Expected compose service is missing: {service}",
                    )
                )
                continue
            if not re.search(r"(?m)^\s+user:\s*[\"']?[^\s\"']+", body):
                findings.append(
                    HygieneFinding(
                        code="compose_service_missing_user",
                        path=_repo_relative(compose, repo_root),
                        detail=f"Compose service does not declare an explicit user: {service}",
                    )
                )

    workflow = repo_root / CI_WORKFLOW
    if workflow.exists():
        text = workflow.read_text(encoding="utf-8")
        for relative_path in SERVICE_DOCKERFILES:
            if relative_path.as_posix() not in text:
                findings.append(
                    HygieneFinding(
                        code="ci_hadolint_missing_service_dockerfile",
                        path=_repo_relative(workflow, repo_root),
                        detail=f"CI Dockerfile lint does not include {relative_path.as_posix()}",
                    )
                )

    return findings


def check_supervisor_log_rotation(repo_root: Path) -> list[HygieneFinding]:
    path = repo_root / SUPERVISOR_CONFIG
    if not path.exists():
        return []

    findings: list[HygieneFinding] = []
    for match in SUPERVISOR_SECTION_PATTERN.finditer(path.read_text(encoding="utf-8")):
        section = match.group("section")
        body = match.group("body")
        for key in ("logfile", "stdout_logfile", "stderr_logfile"):
            if not re.search(rf"(?m)^{key}\s*=", body):
                continue
            if not re.search(rf"(?m)^{key}_maxbytes\s*=\s*[^\\s#]+", body):
                findings.append(
                    HygieneFinding(
                        code="supervisor_log_rotation_missing",
                        path=_repo_relative(path, repo_root),
                        detail=f"[{section}] {key} is missing {key}_maxbytes",
                    )
                )
            if not re.search(rf"(?m)^{key}_backups\s*=\s*[^\\s#]+", body):
                findings.append(
                    HygieneFinding(
                        code="supervisor_log_rotation_missing",
                        path=_repo_relative(path, repo_root),
                        detail=f"[{section}] {key} is missing {key}_backups",
                    )
                )
    return findings


def check_runtime_artifact_ignore_rules(repo_root: Path) -> list[HygieneFinding]:
    path = repo_root / GITIGNORE_FILE
    if not path.exists():
        return []

    patterns = {
        _normalize_ignore_pattern(_strip_comment(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if _normalize_ignore_pattern(_strip_comment(line))
    }
    findings: list[HygieneFinding] = []
    for pattern in REQUIRED_RUNTIME_IGNORE_PATTERNS:
        if pattern not in patterns:
            findings.append(
                HygieneFinding(
                    code="runtime_artifact_ignore_missing",
                    path=_repo_relative(path, repo_root),
                    detail=f"Missing required runtime artifact ignore pattern: {pattern}",
                )
            )
    return findings


def check_local_security_hygiene(repo_root: Path, *, scope: str = "all") -> list[HygieneFinding]:
    if scope not in CHECK_SCOPES:
        raise ValueError(f"unknown scope: {scope}")
    repo_root = repo_root.resolve()
    findings: list[HygieneFinding] = []
    if scope in {"all", "local-dirs"}:
        findings.extend(check_sensitive_local_dirs(repo_root))
    if scope in {"all", "workflow"}:
        findings.extend(check_postgres_release_gate_workflow(repo_root))
        findings.extend(check_milvus_compose_hygiene(repo_root))
        findings.extend(check_production_startup_guards(repo_root))
        findings.extend(check_container_security_config(repo_root))
        findings.extend(check_supervisor_log_rotation(repo_root))
        findings.extend(check_runtime_artifact_ignore_rules(repo_root))
    return findings


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check local security hygiene without modifying files.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT, help="Repository root to check.")
    parser.add_argument(
        "--scope",
        choices=CHECK_SCOPES,
        default="all",
        help="Check all local hygiene, local directories only, or versioned workflow config only.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    findings = check_local_security_hygiene(args.repo_root, scope=args.scope)
    passed = not findings

    if args.json:
        print(json.dumps({"passed": passed, "findings": [asdict(finding) for finding in findings]}, indent=2))
    elif passed:
        print("PASS local security hygiene")
    else:
        print("FAIL local security hygiene")
        for finding in findings:
            location = finding.path if finding.line is None else f"{finding.path}:{finding.line}"
            print(f"- {location}: {finding.code}: {finding.detail}")

    return 0 if passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
