#!/usr/bin/env python3
"""Capture and verify the additive-only meeting contract boundary.

The baseline is always captured from a committed Git ref. Verification may
inspect the current worktree, but it compares only normalized legacy
contracts. Meeting API paths, meeting database tables, and explicitly named
meeting profile additions are excluded from the legacy surface.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

SCHEMA_VERSION = "siq.meeting.contract-baseline.v1"
VERIFICATION_SCHEMA_VERSION = "siq.meeting.contract-verification.v1"
APPROVED_DELTA_SCHEMA_VERSION = "siq.meeting.contract-approved-delta.v1"
APPROVAL_PENDING = "pending-human-review"
APPROVAL_APPROVED = "approved"
MEETING_API_PREFIX = "/api/meetings/v1"
MEETING_TABLE_PREFIX = "meeting_"
PROFILE_ROOT = Path("agents/hermes/profiles")
ALLOWED_PROFILE_ADDITION_PREFIXES = (
    "agents/hermes/profiles/siq_meeting/",
    "agents/hermes/profiles/siq-meeting/",
)
HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
OPENAPI_OPERATION_KEYS = (
    "operationId",
    "deprecated",
    "parameters",
    "requestBody",
    "responses",
    "security",
    "callbacks",
)
OPENAPI_DOCUMENTATION_KEYS = frozenset({"description", "summary", "example", "examples", "externalDocs"})
SENSITIVE_NAME_RE = re.compile(
    r"(?:^|[_-])(?:api[_-]?key|authorization|credential|hmac|jwt|pass(?:word|wd)?|"
    r"private[_-]?key|secret|token|keyring)(?:$|[_-])",
    re.IGNORECASE,
)
SAFE_REF_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/@{}^~:+-]*")
PROFILE_IGNORED_PARTS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", "dist"})
PROFILE_IGNORED_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".lock",
    ".pid",
    ".pyc",
    ".pyo",
)
RUNTIME_PORT_RE = re.compile(r'^([A-Z][A-Z0-9_]*PORT)="\$\{[^\n]*:-([0-9]{2,5})\}\}"\s*$')
RUNTIME_SERVICE_RE = re.compile(r'^\s*require_free_port\s+"\$([A-Z][A-Z0-9_]*)"\s+"([^"]+)"')
RUNTIME_HEALTH_RE = re.compile(r'^\s*wait_for_http\s+"([^"]+)"\s+"([^"]+)"')
SHA256_RE = re.compile(r"[0-9a-f]{64}")
DIFFERENCE_CONTRACTS = frozenset({"legacy_openapi", "legacy_database", "hermes_profiles", "runtime_metadata"})
DIFFERENCE_CHANGES = frozenset({"added", "changed", "removed"})
DIFFERENCE_FIELDS = frozenset({"contract", "path", "change", "before_sha256", "after_sha256"})
APPROVED_DELTA_FIELDS = frozenset(
    {
        "schema_version",
        "baseline_commit",
        "baseline_snapshot_sha256",
        "contract_schema_version",
        "normalization",
        "reviewed_candidate_commit",
        "candidate_contract_sha256",
        "review_scope",
        "justification",
        "approval",
        "differences",
    }
)
ABSENT_VALUE_SHA256 = hashlib.sha256(b'{"siq_contract_value":"absent"}\n').hexdigest()


class ContractBaselineError(RuntimeError):
    """Expected capture or verification failure."""


def _canonical_json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_json_bytes(value)).hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json_bytes(value)
    with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractBaselineError(f"cannot read contract JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise ContractBaselineError(f"contract JSON must be an object: {path.name}")
    return value


def _is_sensitive_name(value: str) -> bool:
    return bool(SENSITIVE_NAME_RE.search(value.replace(".", "_")))


def _safe_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<configured-url>"
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        return value
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        # Source-declared health URLs commonly use shell placeholders such as
        # $BACKEND_PORT. Preserve that non-secret contract text after removing
        # any userinfo rather than attempting to resolve it.
        host = parsed.netloc.rsplit("@", 1)[-1]
    else:
        if port:
            host = f"{host}:{port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _sanitize_scalar(value: Any, *, sensitive_context: bool = False) -> Any:
    if sensitive_context and value is not None:
        return "<redacted>"
    if isinstance(value, str) and "://" in value:
        return _safe_url(value)
    return value


def _sanitize_openapi(value: Any, *, sensitive_context: bool = False) -> Any:
    if isinstance(value, list):
        return [_sanitize_openapi(item, sensitive_context=sensitive_context) for item in value]
    if not isinstance(value, dict):
        return _sanitize_scalar(value)

    named_sensitive = any(_is_sensitive_name(str(value.get(field) or "")) for field in ("name", "title"))
    local_sensitive = sensitive_context or named_sensitive
    result: dict[str, Any] = {}
    for raw_key in sorted(value):
        key = str(raw_key)
        if key in OPENAPI_DOCUMENTATION_KEYS:
            continue
        child = value[raw_key]
        child_sensitive = local_sensitive or _is_sensitive_name(key)
        if key in {"default", "const", "enum"} and child_sensitive:
            result[key] = "<redacted>" if key != "enum" else ["<redacted>"]
            continue
        result[key] = _sanitize_openapi(child, sensitive_context=child_sensitive)
    return result


def _json_pointer_parts(ref: str) -> tuple[str, ...] | None:
    prefix = "#/"
    if not ref.startswith(prefix):
        return None
    return tuple(part.replace("~1", "/").replace("~0", "~") for part in ref[len(prefix) :].split("/"))


def _resolve_pointer(document: Mapping[str, Any], parts: Sequence[str]) -> Any:
    value: Any = document
    for part in parts:
        if not isinstance(value, Mapping) or part not in value:
            raise ContractBaselineError(f"OpenAPI reference is unresolved: #/{'/'.join(parts)}")
        value = value[part]
    return value


def _find_local_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "$ref" and isinstance(child, str) and child.startswith("#/components/"):
                refs.add(child)
            else:
                refs.update(_find_local_refs(child))
    elif isinstance(value, list):
        for child in value:
            refs.update(_find_local_refs(child))
    return refs


def normalize_openapi(document: Mapping[str, Any]) -> dict[str, Any]:
    paths = document.get("paths") or {}
    if not isinstance(paths, Mapping):
        raise ContractBaselineError("OpenAPI paths must be an object")

    operations: dict[str, Any] = {}
    security_names: set[str] = set()
    for raw_path in sorted(paths):
        path = str(raw_path)
        if path == MEETING_API_PREFIX or path.startswith(f"{MEETING_API_PREFIX}/"):
            continue
        path_item = paths[raw_path]
        if not isinstance(path_item, Mapping):
            continue
        inherited_parameters = path_item.get("parameters")
        methods = sorted(HTTP_METHODS & {str(key).lower() for key in path_item})
        operation_ids = {
            str(value.get("operationId"))
            for method in methods
            for value in (path_item.get(method) or path_item.get(method.upper()),)
            if isinstance(value, Mapping) and value.get("operationId")
        }
        shared_multi_method_id: str | None = None
        if len(methods) > 1 and len(operation_ids) == 1:
            only_id = next(iter(operation_ids))
            suffix = only_id.rsplit("_", 1)[-1].lower()
            if suffix in HTTP_METHODS:
                shared_multi_method_id = f"{only_id.rsplit('_', 1)[0]}_<multi-method>"
        for method in methods:
            operation = path_item.get(method)
            if operation is None:
                operation = path_item.get(method.upper())
            if not isinstance(operation, Mapping):
                continue
            selected = {key: operation[key] for key in OPENAPI_OPERATION_KEYS if key in operation}
            if shared_multi_method_id is not None:
                selected["operationId"] = shared_multi_method_id
            if inherited_parameters is not None:
                selected["path_parameters"] = inherited_parameters
            normalized = _sanitize_openapi(selected)
            operations[f"{method.upper()} {path}"] = normalized
            for security in operation.get("security") or []:
                if isinstance(security, Mapping):
                    security_names.update(str(name) for name in security)

    components: dict[str, dict[str, Any]] = {}
    pending = sorted(_find_local_refs(operations))
    seen: set[str] = set()
    while pending:
        ref = pending.pop(0)
        if ref in seen:
            continue
        seen.add(ref)
        parts = _json_pointer_parts(ref)
        if parts is None or len(parts) != 3 or parts[0] != "components":
            continue
        value = _resolve_pointer(document, parts)
        section, name = parts[1], parts[2]
        sanitized = _sanitize_openapi(value, sensitive_context=_is_sensitive_name(name))
        components.setdefault(section, {})[name] = sanitized
        pending.extend(sorted(_find_local_refs(sanitized) - seen))
        pending.sort()

    raw_components = document.get("components") or {}
    raw_security = raw_components.get("securitySchemes") if isinstance(raw_components, Mapping) else None
    if isinstance(raw_security, Mapping):
        for name in sorted(security_names):
            if name in raw_security:
                components.setdefault("securitySchemes", {})[name] = _sanitize_openapi(
                    raw_security[name], sensitive_context=_is_sensitive_name(name)
                )

    return {
        "openapi_version": str(document.get("openapi") or ""),
        "operations": operations,
        "components": {section: dict(sorted(values.items())) for section, values in sorted(components.items())},
    }


def normalize_database_contract(raw_tables: Mapping[str, Any]) -> dict[str, Any]:
    tables: dict[str, Any] = {}
    for raw_name in sorted(raw_tables):
        name = str(raw_name)
        if name.startswith(MEETING_TABLE_PREFIX) or name.startswith("sqlite_"):
            continue
        table = raw_tables[raw_name]
        if not isinstance(table, Mapping):
            raise ContractBaselineError(f"database table contract is invalid: {name}")
        columns: dict[str, Any] = {}
        for raw_column, raw_value in sorted((table.get("columns") or {}).items()):
            column_name = str(raw_column)
            value = dict(raw_value) if isinstance(raw_value, Mapping) else {"type": str(raw_value)}
            if _is_sensitive_name(column_name) and value.get("default") is not None:
                value["default"] = "<redacted>"
            columns[column_name] = value
        indexes: dict[str, Any] = {}
        for raw_index, raw_value in sorted((table.get("indexes") or {}).items()):
            indexes[str(raw_index)] = raw_value
        tables[name] = {"columns": columns, "indexes": indexes}
    return {"tables": tables}


def _profile_file_is_source(relative: PurePosixPath) -> bool:
    if PROFILE_IGNORED_PARTS & set(relative.parts):
        return False
    lowered = relative.name.lower()
    if lowered in {".env", ".update_check"}:
        return False
    return not lowered.endswith(PROFILE_IGNORED_SUFFIXES)


def hash_hermes_profiles(repo_root: Path) -> dict[str, Any]:
    root = repo_root / PROFILE_ROOT
    files: dict[str, str] = {}
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative_to_profile = PurePosixPath(path.relative_to(root).as_posix())
            if not _profile_file_is_source(relative_to_profile):
                continue
            relative = path.relative_to(repo_root).as_posix()
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"root": PROFILE_ROOT.as_posix(), "files": files}


def _parse_env_defaults(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        name = name.strip()
        if "MEETING" in name.upper() or _is_sensitive_name(name):
            continue
        if not (name.endswith("_PORT") or name.endswith("_ENABLED") or "HEALTH_URL" in name or "READINESS_URL" in name):
            continue
        values[name] = _safe_url(value.strip())
    return dict(sorted(values.items()))


def _parse_startup_defaults(path: Path) -> dict[str, Any]:
    ports: dict[str, int] = {}
    services: dict[str, str] = {}
    health_checks: dict[str, str] = {}
    if not path.is_file():
        return {"ports": ports, "services": services, "health_checks": health_checks}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "MEETING" in line.upper():
            continue
        port_match = RUNTIME_PORT_RE.match(line)
        if port_match:
            ports[port_match.group(1)] = int(port_match.group(2))
        service_match = RUNTIME_SERVICE_RE.match(line)
        if service_match:
            services[service_match.group(1)] = service_match.group(2)
        health_match = RUNTIME_HEALTH_RE.match(line)
        if health_match:
            health_checks[health_match.group(2)] = _safe_url(health_match.group(1))
    return {
        "ports": dict(sorted(ports.items())),
        "services": dict(sorted(services.items())),
        "health_checks": dict(sorted(health_checks.items())),
    }


def _safe_exec_start(value: str, repo_root: Path) -> list[str]:
    try:
        parts = shlex.split(value)
    except ValueError:
        parts = value.split()
    safe: list[str] = []
    for part in parts[:2]:
        if _is_sensitive_name(part) or "://" in part:
            safe.append("<redacted-argument>")
            continue
        text = part.replace(str(repo_root), "<repo>")
        safe.append(text)
    return safe


def _systemd_units(repo_root: Path) -> dict[str, Any]:
    unit_root = repo_root / "infra" / "systemd-user"
    units: dict[str, Any] = {}
    if not unit_root.is_dir():
        return units
    for path in sorted(unit_root.glob("*.service")):
        if "meeting" in path.name.lower():
            continue
        description = ""
        exec_start: list[str] = []
        wanted_by = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("Description="):
                description = line.split("=", 1)[1]
            elif line.startswith("ExecStart="):
                exec_start = _safe_exec_start(line.split("=", 1)[1], repo_root)
            elif line.startswith("WantedBy="):
                wanted_by = line.split("=", 1)[1]
        units[path.name] = {
            "description": description,
            "exec_start": exec_start,
            "wanted_by": wanted_by,
        }
    return units


def runtime_metadata(repo_root: Path, openapi_contract: Mapping[str, Any]) -> dict[str, Any]:
    operation_keys = (openapi_contract.get("operations") or {}).keys()
    health_routes = sorted(
        key
        for key in operation_keys
        if any(marker in key.lower() for marker in ("/health", "/ready", "/readiness", "/livez"))
    )
    return {
        "evidence_kind": "source-declared-defaults",
        "environment_defaults": _parse_env_defaults(repo_root / "infra" / "env" / "local.example"),
        "startup": _parse_startup_defaults(repo_root / "start_all.sh"),
        "systemd_units": _systemd_units(repo_root),
        "legacy_health_routes": health_routes,
    }


def build_snapshot(
    *,
    repo_root: Path,
    source_commit: str,
    requested_ref: str,
    source_kind: str,
    openapi_document: Mapping[str, Any],
    database_contract: Mapping[str, Any],
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40,64}", source_commit):
        raise ContractBaselineError("source commit must be a full hexadecimal object id")
    openapi = normalize_openapi(openapi_document)
    database = normalize_database_contract(database_contract)
    return {
        "schema_version": SCHEMA_VERSION,
        "source": {
            "commit": source_commit,
            "kind": source_kind,
            "requested_ref": requested_ref,
        },
        "normalization": {
            "excluded_api_prefixes": [MEETING_API_PREFIX],
            "excluded_database_prefixes": [MEETING_TABLE_PREFIX],
            "allowed_profile_addition_prefixes": list(ALLOWED_PROFILE_ADDITION_PREFIXES),
            "documentation_fields_excluded": sorted(OPENAPI_DOCUMENTATION_KEYS),
        },
        "legacy_openapi": openapi,
        "legacy_database": database,
        "hermes_profiles": hash_hermes_profiles(repo_root),
        "runtime_metadata": runtime_metadata(repo_root, openapi),
    }


def _repo_relative_or_name(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _run_git(repo_root: Path, args: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise ContractBaselineError(f"git command failed: {' '.join(args[:2])}") from exc
    return completed.stdout.strip()


def _validate_requested_ref(value: str) -> str:
    ref = value.strip()
    if ref.upper() == "WORKTREE":
        return "WORKTREE"
    if not SAFE_REF_RE.fullmatch(ref) or ref.startswith("-"):
        raise ContractBaselineError("Git ref contains unsupported characters")
    return ref


def resolve_git_ref(repo_root: Path, requested_ref: str) -> tuple[str, str]:
    ref = _validate_requested_ref(requested_ref)
    if ref == "WORKTREE":
        return _run_git(repo_root, ["rev-parse", "--verify", "HEAD^{commit}"]), "WORKTREE"
    commit = _run_git(repo_root, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
    if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
        raise ContractBaselineError("Git did not resolve the requested ref to a commit")
    return commit, ref


def default_baseline_ref(repo_root: Path) -> tuple[str, str]:
    for remote_ref in ("refs/remotes/origin/master", "refs/remotes/origin/main"):
        try:
            _run_git(repo_root, ["show-ref", "--verify", remote_ref])
        except ContractBaselineError:
            continue
        commit = _run_git(repo_root, ["merge-base", "HEAD", remote_ref])
        if not re.fullmatch(r"[0-9a-f]{40,64}", commit):
            raise ContractBaselineError("merge-base did not resolve to a commit")
        return commit, f"merge-base(HEAD,{remote_ref.removeprefix('refs/remotes/')})"
    raise ContractBaselineError("no origin/master or origin/main remote ref is available; pass --source-ref explicitly")


def _safe_archive_member(member: tarfile.TarInfo) -> None:
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts:
        raise ContractBaselineError("Git archive contains an unsafe path")
    if member.issym() or member.islnk():
        target = PurePosixPath(member.linkname)
        if target.is_absolute() or ".." in target.parts:
            raise ContractBaselineError("Git archive contains an unsafe link")


@contextmanager
def materialized_source(repo_root: Path, commit: str, *, worktree: bool) -> Iterator[Path]:
    if worktree:
        yield repo_root
        return
    with tempfile.TemporaryDirectory(prefix="siq-meeting-contract-ref-") as temporary:
        target = Path(temporary)
        try:
            process = subprocess.Popen(
                ["git", "archive", "--format=tar", commit],
                cwd=repo_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
        except OSError as exc:
            raise ContractBaselineError("cannot start git archive") from exc
        assert process.stdout is not None
        try:
            with tarfile.open(fileobj=process.stdout, mode="r|") as archive:
                for member in archive:
                    _safe_archive_member(member)
                    archive.extract(member, target, filter="data")
        finally:
            process.stdout.close()
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        return_code = process.wait()
        if return_code:
            raise ContractBaselineError(f"git archive failed: {stderr.strip()[:200] or 'unknown error'}")
        yield target


def _default_python(repo_root: Path) -> Path:
    candidate = repo_root / "apps" / "api" / ".venv" / "bin" / "python"
    return candidate if candidate.is_file() else Path(sys.executable)


def _probe_environment(source_root: Path, temporary_root: Path) -> dict[str, str]:
    environment = {
        "HOME": str(temporary_root / "home"),
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "SIQ_APP_DATABASE_URL": f"sqlite:///{temporary_root / 'contract.db'}",
        "DATABASE_URL": f"sqlite:///{temporary_root / 'contract.db'}",
        "SIQ_DATA_ROOT": str(temporary_root / "data"),
        "SIQ_BACKEND_DATA_ROOT": str(temporary_root / "data" / "backend"),
        "SIQ_RUNTIME_ROOT": str(temporary_root / "runtime"),
        "SIQ_ARTIFACTS_ROOT": str(temporary_root / "artifacts"),
        "SIQ_HERMES_HOME": str(temporary_root / "hermes"),
        "SIQ_HERMES_PROFILES_ROOT": str(source_root / PROFILE_ROOT),
        "SIQ_DEPLOYMENT_PROFILE": "development",
        "SIQ_AUTH_SECRET_KEY": "contract-probe-auth-secret-32-bytes",
        "SIQ_SOURCE_TOKEN_SECRET": "contract-probe-source-secret-32-bytes",
        "SIQ_MEETINGS_ENABLED": "0",
    }
    for name in ("LANG", "LC_ALL", "LD_LIBRARY_PATH", "VIRTUAL_ENV"):
        if os.environ.get(name):
            environment[name] = os.environ[name]
    return environment


def run_source_probe(
    *,
    repo_root: Path,
    source_root: Path,
    python_executable: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    with tempfile.TemporaryDirectory(prefix="siq-meeting-contract-probe-") as temporary:
        temporary_root = Path(temporary)
        output = temporary_root / "probe.json"
        command = [
            str(python_executable),
            str(Path(__file__).resolve()),
            "_probe",
            "--source-root",
            str(source_root),
            "--output",
            str(output),
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=source_root / "apps" / "api",
                env=_probe_environment(source_root, temporary_root),
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ContractBaselineError("isolated API contract probe did not complete") from exc
        if completed.returncode:
            stderr = completed.stderr.replace(str(source_root), "<source>").replace(str(temporary_root), "<temp>")
            raise ContractBaselineError(
                f"isolated API contract probe failed: {stderr.strip()[-1000:] or 'no diagnostic'}"
            )
        probe = _read_json(output)
        openapi = probe.get("openapi")
        database = probe.get("database")
        if not isinstance(openapi, dict) or not isinstance(database, dict):
            raise ContractBaselineError("isolated API contract probe returned an invalid payload")
        return openapi, database


def capture_source(
    *,
    repo_root: Path,
    commit: str,
    requested_ref: str,
    worktree: bool,
    python_executable: Path,
) -> dict[str, Any]:
    with materialized_source(repo_root, commit, worktree=worktree) as source_root:
        openapi, database = run_source_probe(
            repo_root=repo_root,
            source_root=source_root,
            python_executable=python_executable,
        )
        return build_snapshot(
            repo_root=source_root,
            source_commit=commit,
            requested_ref=requested_ref,
            source_kind="worktree" if worktree else "git-ref",
            openapi_document=openapi,
            database_contract=database,
        )


def _pointer_escape(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _difference(
    *,
    contract: str,
    path: str,
    change: str,
    before: Any = None,
    after: Any = None,
    before_sha256: str | None = None,
    after_sha256: str | None = None,
) -> dict[str, str]:
    return {
        "contract": contract,
        "path": path,
        "change": change,
        "before_sha256": (
            before_sha256
            if before_sha256 is not None
            else ABSENT_VALUE_SHA256
            if change == "added"
            else _digest(before)
        ),
        "after_sha256": (
            after_sha256 if after_sha256 is not None else ABSENT_VALUE_SHA256 if change == "removed" else _digest(after)
        ),
    }


def _difference_key(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return (str(item.get("contract")), str(item.get("path")), str(item.get("change")))


def _deep_differences(
    before: Any,
    after: Any,
    *,
    contract: str,
    path: str = "",
) -> list[dict[str, Any]]:
    if isinstance(before, Mapping) and isinstance(after, Mapping):
        differences: list[dict[str, Any]] = []
        for key in sorted(set(before) | set(after), key=str):
            child_path = f"{path}/{_pointer_escape(str(key))}"
            if key not in before:
                differences.append(
                    _difference(
                        contract=contract,
                        path=child_path,
                        change="added",
                        after=after[key],
                    )
                )
            elif key not in after:
                differences.append(
                    _difference(
                        contract=contract,
                        path=child_path,
                        change="removed",
                        before=before[key],
                    )
                )
            else:
                differences.extend(_deep_differences(before[key], after[key], contract=contract, path=child_path))
        return differences
    if before == after:
        return []
    return [
        _difference(
            contract=contract,
            path=path or "/",
            change="changed",
            before=before,
            after=after,
        )
    ]


def _profile_differences(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    baseline_files = before.get("files") or {}
    candidate_files = after.get("files") or {}
    if not isinstance(baseline_files, Mapping) or not isinstance(candidate_files, Mapping):
        return _deep_differences(before, after, contract="hermes_profiles")
    differences: list[dict[str, Any]] = []
    for path in sorted(set(baseline_files) | set(candidate_files)):
        if path not in baseline_files:
            if any(str(path).startswith(prefix) for prefix in ALLOWED_PROFILE_ADDITION_PREFIXES):
                continue
            differences.append(
                _difference(
                    contract="hermes_profiles",
                    path=f"/files/{_pointer_escape(str(path))}",
                    change="added",
                    after_sha256=str(candidate_files[path]),
                )
            )
        elif path not in candidate_files:
            differences.append(
                _difference(
                    contract="hermes_profiles",
                    path=f"/files/{_pointer_escape(str(path))}",
                    change="removed",
                    before_sha256=str(baseline_files[path]),
                )
            )
        elif baseline_files[path] != candidate_files[path]:
            differences.append(
                _difference(
                    contract="hermes_profiles",
                    path=f"/files/{_pointer_escape(str(path))}",
                    change="changed",
                    before_sha256=str(baseline_files[path]),
                    after_sha256=str(candidate_files[path]),
                )
            )
    return differences


def _validate_snapshot_pair(
    baseline: Mapping[str, Any], candidate: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    if baseline.get("schema_version") != SCHEMA_VERSION:
        raise ContractBaselineError("baseline schema version is unsupported")
    if candidate.get("schema_version") != SCHEMA_VERSION:
        raise ContractBaselineError("candidate schema version is unsupported")
    if baseline.get("normalization") != candidate.get("normalization"):
        raise ContractBaselineError("baseline and candidate normalization policies differ")
    baseline_source = baseline.get("source")
    candidate_source = candidate.get("source")
    if not isinstance(baseline_source, Mapping) or not re.fullmatch(
        r"[0-9a-f]{40,64}", str(baseline_source.get("commit") or "")
    ):
        raise ContractBaselineError("baseline source commit is invalid")
    if baseline_source.get("kind") != "git-ref":
        raise ContractBaselineError("baseline must have been captured from a committed Git ref")
    if not isinstance(candidate_source, Mapping) or not re.fullmatch(
        r"[0-9a-f]{40,64}", str(candidate_source.get("commit") or "")
    ):
        raise ContractBaselineError("candidate source commit is invalid")
    return baseline_source, candidate_source


def _snapshot_differences(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> list[dict[str, Any]]:
    _validate_snapshot_pair(baseline, candidate)

    differences: list[dict[str, Any]] = []
    for contract in ("legacy_openapi", "legacy_database", "runtime_metadata"):
        differences.extend(
            _deep_differences(
                baseline.get(contract),
                candidate.get(contract),
                contract=contract,
            )
        )
    differences.extend(
        _profile_differences(
            baseline.get("hermes_profiles") or {},
            candidate.get("hermes_profiles") or {},
        )
    )
    differences.sort(key=_difference_key)
    return differences


def _candidate_contract_digest(candidate: Mapping[str, Any]) -> str:
    """Hash only the monitored contract; source commit/ref metadata is provenance."""
    return _digest(
        {
            key: candidate.get(key)
            for key in (
                "schema_version",
                "normalization",
                "legacy_openapi",
                "legacy_database",
                "hermes_profiles",
                "runtime_metadata",
            )
        }
    )


def _require_nonempty_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractBaselineError(f"approved delta {field} must be a non-empty string")
    return value.strip()


def _validate_approved_differences(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ContractBaselineError("approved delta differences must be an array")
    differences: list[dict[str, str]] = []
    for position, raw_item in enumerate(value):
        if not isinstance(raw_item, Mapping):
            raise ContractBaselineError(f"approved delta difference {position} must be an object")
        if set(raw_item) != DIFFERENCE_FIELDS:
            raise ContractBaselineError(f"approved delta difference {position} fields are invalid")
        item = {field: raw_item[field] for field in DIFFERENCE_FIELDS}
        if item["contract"] not in DIFFERENCE_CONTRACTS:
            raise ContractBaselineError(f"approved delta difference {position} contract is invalid")
        if item["change"] not in DIFFERENCE_CHANGES:
            raise ContractBaselineError(f"approved delta difference {position} change is invalid")
        path = item["path"]
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or any(marker in path for marker in ("*", "?", "[", "]"))
        ):
            raise ContractBaselineError(
                f"approved delta difference {position} path must be exact; glob/prefix rules are forbidden"
            )
        for field in ("before_sha256", "after_sha256"):
            if not isinstance(item[field], str) or not SHA256_RE.fullmatch(item[field]):
                raise ContractBaselineError(f"approved delta difference {position} {field} is invalid")
        differences.append(item)

    keys = [_difference_key(item) for item in differences]
    if len(keys) != len(set(keys)):
        raise ContractBaselineError("approved delta differences must be unique")
    if keys != sorted(keys):
        raise ContractBaselineError("approved delta differences must be sorted")
    return differences


def _inspect_approved_delta(
    artifact: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> tuple[list[dict[str, str]], str, bool]:
    baseline_source, _candidate_source = _validate_snapshot_pair(baseline, candidate)
    if set(artifact) != APPROVED_DELTA_FIELDS:
        raise ContractBaselineError("approved delta top-level fields are invalid")
    if artifact.get("schema_version") != APPROVED_DELTA_SCHEMA_VERSION:
        raise ContractBaselineError("approved delta schema version is unsupported")
    if artifact.get("contract_schema_version") != SCHEMA_VERSION:
        raise ContractBaselineError("approved delta contract schema version is unsupported")
    if artifact.get("baseline_commit") != baseline_source.get("commit"):
        raise ContractBaselineError("approved delta baseline commit does not match the baseline")
    if artifact.get("baseline_snapshot_sha256") != _digest(baseline):
        raise ContractBaselineError("approved delta baseline snapshot SHA-256 does not match")
    if artifact.get("normalization") != baseline.get("normalization"):
        raise ContractBaselineError("approved delta normalization does not match the baseline")
    reviewed_candidate_commit = artifact.get("reviewed_candidate_commit")
    if not isinstance(reviewed_candidate_commit, str) or not re.fullmatch(
        r"[0-9a-f]{40,64}", reviewed_candidate_commit
    ):
        raise ContractBaselineError("approved delta reviewed candidate commit is invalid")
    candidate_contract_sha256 = artifact.get("candidate_contract_sha256")
    if not isinstance(candidate_contract_sha256, str) or not SHA256_RE.fullmatch(candidate_contract_sha256):
        raise ContractBaselineError("approved delta candidate contract SHA-256 is invalid")
    _require_nonempty_string(artifact.get("review_scope"), "review_scope")
    _require_nonempty_string(artifact.get("justification"), "justification")

    approval = artifact.get("approval")
    if not isinstance(approval, Mapping) or set(approval) != {"status", "reviewed_by"}:
        raise ContractBaselineError("approved delta approval fields are invalid")
    status = approval.get("status")
    reviewed_by = approval.get("reviewed_by")
    if status == APPROVAL_PENDING:
        if reviewed_by is not None:
            raise ContractBaselineError("pending approved delta must not name a reviewer")
    elif status == APPROVAL_APPROVED:
        _require_nonempty_string(reviewed_by, "approval.reviewed_by")
    else:
        raise ContractBaselineError("approved delta approval status is invalid")

    return (
        _validate_approved_differences(artifact.get("differences")),
        str(status),
        candidate_contract_sha256 == _candidate_contract_digest(candidate),
    )


def validate_approved_delta(
    artifact: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
) -> tuple[list[dict[str, str]], str]:
    differences, status, candidate_contract_match = _inspect_approved_delta(
        artifact,
        baseline=baseline,
        candidate=candidate,
    )
    if not candidate_contract_match:
        raise ContractBaselineError("approved delta candidate contract SHA-256 does not match the current candidate")
    return differences, status


def build_approved_delta(
    *,
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    review_scope: str,
    justification: str,
) -> dict[str, Any]:
    baseline_source, candidate_source = _validate_snapshot_pair(baseline, candidate)
    if candidate_source.get("kind") != "git-ref":
        raise ContractBaselineError("approved delta capture requires a candidate from a committed Git ref")
    return {
        "schema_version": APPROVED_DELTA_SCHEMA_VERSION,
        "baseline_commit": baseline_source["commit"],
        "baseline_snapshot_sha256": _digest(baseline),
        "contract_schema_version": SCHEMA_VERSION,
        "normalization": json.loads(json.dumps(baseline["normalization"])),
        "reviewed_candidate_commit": candidate_source["commit"],
        "candidate_contract_sha256": _candidate_contract_digest(candidate),
        "review_scope": _require_nonempty_string(review_scope, "review_scope"),
        "justification": _require_nonempty_string(justification, "justification"),
        "approval": {
            "status": APPROVAL_PENDING,
            "reviewed_by": None,
        },
        "differences": _snapshot_differences(baseline, candidate),
    }


def _compare_approved_differences(
    approved: Sequence[Mapping[str, str]],
    actual: Sequence[Mapping[str, str]],
) -> tuple[list[dict[str, str]], list[dict[str, str]], list[dict[str, Any]]]:
    approved_by_key = {_difference_key(item): dict(item) for item in approved}
    actual_by_key = {_difference_key(item): dict(item) for item in actual}
    missing = [approved_by_key[key] for key in sorted(approved_by_key.keys() - actual_by_key.keys())]
    unexpected = [actual_by_key[key] for key in sorted(actual_by_key.keys() - approved_by_key.keys())]
    mismatched: list[dict[str, Any]] = []
    for key in sorted(approved_by_key.keys() & actual_by_key.keys()):
        expected = approved_by_key[key]
        observed = actual_by_key[key]
        if expected == observed:
            continue
        mismatched.append(
            {
                "contract": key[0],
                "path": key[1],
                "change": key[2],
                "fields": sorted(field for field in DIFFERENCE_FIELDS if expected[field] != observed[field]),
                "approved": expected,
                "actual": observed,
            }
        )
    return missing, unexpected, mismatched


def compare_snapshots(
    baseline: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    approved_delta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    baseline_source, candidate_source = _validate_snapshot_pair(baseline, candidate)
    differences = _snapshot_differences(baseline, candidate)
    missing: list[dict[str, str]] = []
    unexpected: list[dict[str, str]] = []
    mismatched: list[dict[str, Any]] = []
    approval_status = "not-required" if not differences else "missing"
    approved_difference_count = 0

    if approved_delta is not None:
        approved, artifact_status, candidate_contract_match = _inspect_approved_delta(
            approved_delta,
            baseline=baseline,
            candidate=candidate,
        )
        approved_difference_count = len(approved)
        missing, unexpected, mismatched = _compare_approved_differences(approved, differences)
        approval_status = artifact_status
    else:
        candidate_contract_match = False
    if approved_delta is None and differences:
        unexpected = list(differences)

    exact_match = not missing and not unexpected and not mismatched
    passed = (not differences and approved_delta is None) or (
        approved_delta is not None and approval_status == APPROVAL_APPROVED and candidate_contract_match and exact_match
    )
    return {
        "schema_version": VERIFICATION_SCHEMA_VERSION,
        "passed": passed,
        "baseline_commit": baseline_source.get("commit"),
        "baseline_snapshot_sha256": _digest(baseline),
        "candidate_commit": candidate_source.get("commit"),
        "candidate_kind": candidate_source.get("kind"),
        "candidate_contract_sha256": _candidate_contract_digest(candidate),
        "approved_candidate_contract_sha256": (
            approved_delta.get("candidate_contract_sha256") if approved_delta else None
        ),
        "candidate_contract_match": candidate_contract_match if approved_delta else None,
        "difference_count": len(differences),
        "differences": differences,
        "approved_delta_status": approval_status,
        "approved_difference_count": approved_difference_count,
        "missing_differences": missing,
        "unexpected_differences": unexpected,
        "mismatched_differences": mismatched,
    }


def _database_probe() -> dict[str, Any]:
    import database  # type: ignore[import-not-found]
    from sqlalchemy import inspect  # type: ignore[import-not-found]

    database.create_db_and_tables()
    inspector = inspect(database.engine)
    tables: dict[str, Any] = {}
    for table_name in sorted(inspector.get_table_names()):
        columns: dict[str, Any] = {}
        for column in inspector.get_columns(table_name):
            name = str(column["name"])
            default = column.get("default")
            columns[name] = {
                "type": str(column.get("type") or ""),
                "nullable": bool(column.get("nullable")),
                "primary_key": bool(column.get("primary_key")),
                "default": None if default is None else str(default),
            }
        indexes: dict[str, Any] = {}
        for position, index in enumerate(inspector.get_indexes(table_name)):
            index_name = str(index.get("name") or f"<unnamed-{position}>")
            indexes[index_name] = {
                "columns": [str(item) for item in index.get("column_names") or []],
                "unique": bool(index.get("unique")),
            }
        tables[table_name] = {"columns": columns, "indexes": indexes}
    return tables


def _probe_main(args: argparse.Namespace) -> int:
    source_root = args.source_root.resolve()
    api_root = source_root / "apps" / "api"
    if not api_root.is_dir():
        raise ContractBaselineError("source root does not contain apps/api")
    sys.path.insert(0, str(api_root))
    os.chdir(api_root)
    try:
        import main  # type: ignore[import-not-found]
    except Exception as exc:
        raise ContractBaselineError(f"cannot import API app: {type(exc).__name__}") from exc
    payload = {
        "openapi": main.app.openapi(),
        "database": _database_probe(),
    }
    _write_json(args.output, payload)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="repository root (default: inferred from this script)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="capture a deterministic baseline from a Git ref")
    capture.add_argument(
        "--source-ref",
        help="committed Git ref; defaults to merge-base(HEAD, origin/master or origin/main)",
    )
    capture.add_argument("--python", type=Path, help="Python with apps/api dependencies installed")
    capture.add_argument("--output", type=Path, required=True)

    verify = subparsers.add_parser("verify", help="verify a ref or worktree against a captured baseline")
    verify.add_argument("--baseline", type=Path, required=True)
    verify.add_argument(
        "--approved-delta",
        type=Path,
        help="exact, human-approved delta artifact; absent deltas fail closed",
    )
    verify.add_argument("--candidate-ref", default="WORKTREE")
    verify.add_argument("--python", type=Path, help="Python with apps/api dependencies installed")
    verify.add_argument("--report", type=Path)

    capture_delta = subparsers.add_parser(
        "capture-approved-delta",
        help="capture a pending exact delta from a committed candidate for human review",
    )
    capture_delta.add_argument("--baseline", type=Path, required=True)
    capture_delta.add_argument("--candidate-ref", required=True)
    capture_delta.add_argument("--python", type=Path, help="Python with apps/api dependencies installed")
    capture_delta.add_argument("--review-scope", required=True)
    capture_delta.add_argument("--justification", required=True)
    capture_delta.add_argument("--output", type=Path, required=True)

    probe = subparsers.add_parser("_probe", help=argparse.SUPPRESS)
    probe.add_argument("--source-root", type=Path, required=True)
    probe.add_argument("--output", type=Path, required=True)
    return parser


def _capture_command(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    if args.source_ref:
        if args.source_ref.upper() == "WORKTREE":
            raise ContractBaselineError("capture requires a committed Git ref, not WORKTREE")
        commit, requested_ref = resolve_git_ref(repo_root, args.source_ref)
    else:
        commit, requested_ref = default_baseline_ref(repo_root)
    python_executable = (args.python or _default_python(repo_root)).resolve()
    snapshot = capture_source(
        repo_root=repo_root,
        commit=commit,
        requested_ref=requested_ref,
        worktree=False,
        python_executable=python_executable,
    )
    _write_json(args.output, snapshot)
    print(
        json.dumps(
            {
                "status": "captured",
                "source_commit": commit,
                "source_ref": requested_ref,
                "output": _repo_relative_or_name(args.output, repo_root),
                "snapshot_sha256": _digest(snapshot),
            },
            sort_keys=True,
        )
    )
    return 0


def _verify_command(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    baseline = _read_json(args.baseline)
    commit, requested_ref = resolve_git_ref(repo_root, args.candidate_ref)
    worktree = requested_ref == "WORKTREE"
    python_executable = (args.python or _default_python(repo_root)).resolve()
    candidate = capture_source(
        repo_root=repo_root,
        commit=commit,
        requested_ref=requested_ref,
        worktree=worktree,
        python_executable=python_executable,
    )
    approved_delta = _read_json(args.approved_delta) if args.approved_delta else None
    report = compare_snapshots(baseline, candidate, approved_delta=approved_delta)
    if args.report:
        _write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


def _capture_approved_delta_command(args: argparse.Namespace) -> int:
    repo_root = args.repo_root.resolve()
    if args.candidate_ref.upper() == "WORKTREE":
        raise ContractBaselineError("approved delta capture requires a committed Git ref, not WORKTREE")
    baseline = _read_json(args.baseline)
    commit, requested_ref = resolve_git_ref(repo_root, args.candidate_ref)
    python_executable = (args.python or _default_python(repo_root)).resolve()
    candidate = capture_source(
        repo_root=repo_root,
        commit=commit,
        requested_ref=requested_ref,
        worktree=False,
        python_executable=python_executable,
    )
    artifact = build_approved_delta(
        baseline=baseline,
        candidate=candidate,
        review_scope=args.review_scope,
        justification=args.justification,
    )
    validate_approved_delta(artifact, baseline=baseline, candidate=candidate)
    _write_json(args.output, artifact)
    print(
        json.dumps(
            {
                "status": APPROVAL_PENDING,
                "reviewed_candidate_commit": commit,
                "difference_count": len(artifact["differences"]),
                "output": _repo_relative_or_name(args.output, repo_root),
                "note": "capture is evidence for review and is not human approval",
            },
            sort_keys=True,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "_probe":
            return _probe_main(args)
        if args.command == "capture":
            return _capture_command(args)
        if args.command == "capture-approved-delta":
            return _capture_approved_delta_command(args)
        if args.command == "verify":
            return _verify_command(args)
        parser.error("unknown command")
    except ContractBaselineError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
