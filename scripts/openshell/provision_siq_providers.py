#!/usr/bin/env python3
"""Validate and explicitly provision SIQ OpenShell provider credentials.

Dry-run is the default. It performs only local, secret-free validation and
prints provider names plus a deterministic summary hash. Real gateway changes
require --apply and an exact gateway confirmation.
"""

from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import hmac
import json
import os
import pwd
import re
import stat
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROVIDER_ROOT = PROJECT_ROOT / "infra" / "openshell" / "providers"
MANIFEST_PATH = PROVIDER_ROOT / "manifest.json"
UPSTREAM_VERSION_PATH = PROJECT_ROOT / "infra" / "openshell" / "upstream-version.json"
CLI_PATH = PROJECT_ROOT / "scripts" / "openshell" / "run_cli.sh"

PINNED_OPENSHELL_VERSION = "0.0.83"
EXPECTED_GATEWAY = "siq-openshell-dev"
EXPECTED_GATEWAY_ENDPOINT = "https://127.0.0.1:17671"
EXPECTED_GATEWAY_TYPE = "local"
EXPECTED_GATEWAY_AUTH = "mtls"
TAVILY_BODY_REWRITE_MAX_BYTES = 256 * 1024
MAX_SECRET_FILE_BYTES = 1024 * 1024
MAX_SECRET_VALUE_BYTES = 16 * 1024
PYTHON_BINARY = "/opt/siq/hermes/venv/bin/python"
MINIMAX_PROVIDER_NAME = "siq-minimax-cn-pool"
MAINTENANCE_LOCK_PATH = PROJECT_ROOT / "var" / "openshell" / "locks" / "maintenance.lock"
REVIEWED_SEARCH_ROUTES = {
    "siq-tavily-search": frozenset(
        {
            ("api.tavily.com", "POST", "/search"),
            ("api.tavily.com", "POST", "/extract"),
            ("api.tavily.com", "POST", "/crawl"),
            ("api.tavily.com", "POST", "/map"),
            ("api.tavily.com", "POST", "/research"),
            ("api.tavily.com", "GET", "/research/**"),
        }
    ),
    "siq-exa-search": frozenset(
        {
            ("api.exa.ai", "POST", "/search"),
            ("api.exa.ai", "POST", "/contents"),
            ("api.exa.ai", "POST", "/answer"),
            ("api.exa.ai", "POST", "/context"),
            ("api.exa.ai", "POST", "/agent/runs"),
            ("api.exa.ai", "GET", "/agent/runs/**"),
        }
    ),
}

ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$")


class ProvisionError(RuntimeError):
    """An intentionally secret-free operator error."""


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    profile_id: str
    profile_path: Path
    credential_keys: tuple[str, ...]


@dataclass(frozen=True)
class ProvisionPlan:
    manifest: Mapping[str, Any]
    specs: tuple[ProviderSpec, ...]
    profiles: Mapping[str, Mapping[str, Any]]
    hermes_auth_template: Mapping[str, Any]
    summary_sha256: str

    @property
    def provider_names(self) -> list[str]:
        return [spec.name for spec in self.specs]


@dataclass(frozen=True)
class CliResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class Runner(Protocol):
    def run(
        self,
        arguments: Sequence[str],
        *,
        credential_env: Mapping[str, str] | None = None,
    ) -> CliResult: ...


class SubprocessRunner:
    """Run the pinned project wrapper without forwarding captured output."""

    def run(
        self,
        arguments: Sequence[str],
        *,
        credential_env: Mapping[str, str] | None = None,
    ) -> CliResult:
        completed = subprocess.run(
            [str(CLI_PATH), *arguments],
            cwd=PROJECT_ROOT,
            env=_minimal_child_environment(credential_env or {}),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=45,
            check=False,
        )
        return CliResult(completed.returncode, completed.stdout, completed.stderr)


def _require_keys(
    value: Mapping[str, Any],
    *,
    required: set[str],
    allowed: set[str],
    label: str,
) -> None:
    missing = required - set(value)
    unknown = set(value) - allowed
    if missing:
        raise ProvisionError(f"{label} is missing required fields")
    if unknown:
        raise ProvisionError(f"{label} contains fields outside the reviewed 0.0.83 subset")


def _asset_path(relative: str, *, suffixes: set[str] | None = None) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ProvisionError("provider manifest contains an invalid asset path")
    candidate = PROVIDER_ROOT / relative
    current = PROVIDER_ROOT
    for component in Path(relative).parts:
        if component in {"", ".", ".."}:
            raise ProvisionError("provider manifest contains an invalid asset path")
        current = current / component
        if current.is_symlink():
            raise ProvisionError("provider assets must not contain symlinks")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(PROVIDER_ROOT.resolve(strict=True))
    except (FileNotFoundError, ValueError, OSError) as exc:
        raise ProvisionError("provider manifest references an unavailable asset") from exc
    if not resolved.is_file():
        raise ProvisionError("provider manifest asset is not a regular file")
    if suffixes is not None and resolved.suffix.lower() not in suffixes:
        raise ProvisionError("provider manifest asset has an unexpected file type")
    return resolved


def _load_json(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProvisionError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ProvisionError(f"{label} must be a JSON object")
    return value


def _load_profile(path: Path) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProvisionError("provider profile is not valid YAML") from exc
    if not isinstance(value, dict):
        raise ProvisionError("provider profile must be a YAML object")
    return value


def _validate_profile(profile: Mapping[str, Any], spec: ProviderSpec) -> None:
    _require_keys(
        profile,
        required={
            "id",
            "display_name",
            "description",
            "category",
            "inference_capable",
            "credentials",
            "endpoints",
            "binaries",
        },
        allowed={
            "id",
            "display_name",
            "description",
            "category",
            "inference_capable",
            "credentials",
            "endpoints",
            "binaries",
        },
        label="provider profile",
    )
    if profile["id"] != spec.profile_id or not PROFILE_ID_RE.fullmatch(spec.profile_id):
        raise ProvisionError("provider profile id does not match its manifest entry")
    if profile["category"] not in {"inference", "knowledge"}:
        raise ProvisionError("provider profile category is outside the reviewed set")
    if type(profile["inference_capable"]) is not bool:
        raise ProvisionError("provider profile inference_capable must be boolean")
    if not isinstance(profile["display_name"], str) or not profile["display_name"].strip():
        raise ProvisionError("provider profile display_name is required")
    if not isinstance(profile["description"], str) or not profile["description"].strip():
        raise ProvisionError("provider profile description is required")

    credentials = profile["credentials"]
    if not isinstance(credentials, list) or not credentials:
        raise ProvisionError("provider profile requires credentials")
    flattened_env_vars: list[str] = []
    credential_names: set[str] = set()
    for credential in credentials:
        if not isinstance(credential, dict):
            raise ProvisionError("provider credential must be an object")
        _require_keys(
            credential,
            required={"name", "description", "env_vars", "required"},
            allowed={
                "name",
                "description",
                "env_vars",
                "required",
                "auth_style",
                "header_name",
            },
            label="provider credential",
        )
        name = credential["name"]
        if not isinstance(name, str) or not name or name in credential_names:
            raise ProvisionError("provider credential names must be unique")
        credential_names.add(name)
        if credential["required"] is not True:
            raise ProvisionError("all SIQ provider credentials must be required")
        env_vars = credential["env_vars"]
        if not isinstance(env_vars, list) or len(env_vars) != 1:
            raise ProvisionError("each SIQ provider credential must declare one env var")
        env_var = env_vars[0]
        if not isinstance(env_var, str) or not ENV_NAME_RE.fullmatch(env_var):
            raise ProvisionError("provider credential env var is invalid")
        if env_var in flattened_env_vars:
            raise ProvisionError("provider credential env vars must be unique")
        flattened_env_vars.append(env_var)
        auth_style = credential.get("auth_style", "")
        if auth_style not in {"", "bearer", "header"}:
            raise ProvisionError("provider credential auth style is not reviewed")
        if auth_style in {"bearer", "header"}:
            header = credential.get("header_name")
            if not isinstance(header, str) or not header:
                raise ProvisionError("header credential is missing header_name")
        elif "header_name" in credential:
            raise ProvisionError("header_name requires a header credential style")
    if tuple(flattened_env_vars) != spec.credential_keys:
        raise ProvisionError("provider credential env vars do not match the manifest")

    endpoints = profile["endpoints"]
    if not isinstance(endpoints, list) or not endpoints:
        raise ProvisionError("provider profile requires at least one endpoint")
    rewrite_count = 0
    endpoint_rules: set[tuple[str, str, str]] = set()
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            raise ProvisionError("provider endpoint must be an object")
        _require_keys(
            endpoint,
            required={"host", "port", "protocol", "enforcement", "rules"},
            allowed={
                "host",
                "port",
                "protocol",
                "enforcement",
                "rules",
                "request_body_credential_rewrite",
            },
            label="provider endpoint",
        )
        host = endpoint["host"]
        if not isinstance(host, str) or not HOST_RE.fullmatch(host) or "*" in host:
            raise ProvisionError("provider endpoint host must be an exact DNS name")
        if endpoint["port"] != 443:
            raise ProvisionError("SIQ external provider endpoints must use port 443")
        if endpoint["protocol"] != "rest" or endpoint["enforcement"] != "enforce":
            raise ProvisionError("SIQ provider endpoints require enforced REST inspection")
        rewrite = endpoint.get("request_body_credential_rewrite", False)
        if type(rewrite) is not bool:
            raise ProvisionError("request body credential rewrite must be boolean")
        rewrite_count += int(rewrite)
        rules = endpoint["rules"]
        if not isinstance(rules, list) or not rules:
            raise ProvisionError("provider REST endpoints require explicit allow rules")
        for rule in rules:
            if not isinstance(rule, dict):
                raise ProvisionError("provider REST rule must be an object")
            _require_keys(
                rule,
                required={"allow"},
                allowed={"allow"},
                label="provider REST rule",
            )
            allow = rule["allow"]
            if not isinstance(allow, dict):
                raise ProvisionError("provider REST allow rule must be an object")
            _require_keys(
                allow,
                required={"method", "path"},
                allowed={"method", "path"},
                label="provider REST allow rule",
            )
            method = allow["method"]
            path = allow["path"]
            if method not in {"GET", "POST"}:
                raise ProvisionError("provider REST method is outside the reviewed set")
            if not isinstance(path, str) or not path.startswith("/"):
                raise ProvisionError("provider REST paths must be absolute")
            if "*" in path and (not path.endswith("/**") or path.count("*") != 2):
                raise ProvisionError("provider REST path wildcard is outside the reviewed prefix form")
            signature = (host, method, path)
            if signature in endpoint_rules:
                raise ProvisionError("provider REST rules must be unique")
            endpoint_rules.add(signature)
    if spec.name == "siq-tavily-search":
        if rewrite_count != 1:
            raise ProvisionError("Tavily must enable request-body credential rewriting")
    elif rewrite_count:
        raise ProvisionError("only Tavily may enable request-body credential rewriting")
    if spec.name in REVIEWED_SEARCH_ROUTES and endpoint_rules != REVIEWED_SEARCH_ROUTES[spec.name]:
        raise ProvisionError("search provider routes differ from the reviewed retrieval-only contract")

    binaries = profile["binaries"]
    if binaries != [PYTHON_BINARY]:
        raise ProvisionError("provider profiles must bind only the reviewed Hermes Python binary")


def _validate_hermes_auth_template(template: Mapping[str, Any], specs: Sequence[ProviderSpec]) -> None:
    _require_keys(
        template,
        required={"version", "providers", "credential_pool"},
        allowed={"version", "providers", "credential_pool"},
        label="Hermes auth template",
    )
    if template["version"] != 1 or template["providers"] != {}:
        raise ProvisionError("Hermes auth template has an unexpected base structure")
    pool_map = template["credential_pool"]
    if not isinstance(pool_map, dict) or set(pool_map) != {"minimax-cn"}:
        raise ProvisionError("Hermes auth template must contain only the MiniMax China pool")
    entries = pool_map["minimax-cn"]
    if not isinstance(entries, list) or len(entries) != 2:
        raise ProvisionError("Hermes MiniMax China template must preserve two credentials")
    minimax_specs = [spec for spec in specs if spec.name == MINIMAX_PROVIDER_NAME]
    if len(minimax_specs) != 1:
        raise ProvisionError("provider manifest must contain one MiniMax pool")
    expected_keys = minimax_specs[0].credential_keys
    expected = (
        ("minimax_cn_primary_0", 0, expected_keys[0]),
        ("minimax_cn_backup_10", 10, expected_keys[1]),
    )
    for entry, (expected_id, expected_priority, expected_key) in zip(entries, expected, strict=True):
        if not isinstance(entry, dict):
            raise ProvisionError("Hermes MiniMax pool entry must be an object")
        if entry.get("id") != expected_id or entry.get("priority") != expected_priority:
            raise ProvisionError("Hermes MiniMax pool order or identity changed")
        if entry.get("source") != "openshell:provider":
            raise ProvisionError("Hermes MiniMax pool source must remain OpenShell")
        if entry.get("base_url") != "https://api.minimax.chat/v1":
            raise ProvisionError("Hermes MiniMax pool host changed")
        expected_placeholder = f"openshell:resolve:env:{expected_key}"
        if entry.get("access_token") != expected_placeholder:
            raise ProvisionError("Hermes MiniMax pool must use exact OpenShell placeholders")


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def load_plan(selected_names: Sequence[str] | None = None) -> ProvisionPlan:
    manifest = _load_json(MANIFEST_PATH, label="provider manifest")
    _require_keys(
        manifest,
        required={
            "schema_version",
            "openshell_version",
            "gateway",
            "gateway_endpoint",
            "gateway_type",
            "gateway_auth",
            "providers_v2_required",
            "request_body_credential_rewrite_max_bytes",
            "hermes_auth_template",
            "providers",
        },
        allowed={
            "schema_version",
            "openshell_version",
            "gateway",
            "gateway_endpoint",
            "gateway_type",
            "gateway_auth",
            "providers_v2_required",
            "request_body_credential_rewrite_max_bytes",
            "hermes_auth_template",
            "providers",
        },
        label="provider manifest",
    )
    if manifest["schema_version"] != "siq.openshell.provider_manifest.v1":
        raise ProvisionError("provider manifest schema version is unsupported")
    if manifest["openshell_version"] != PINNED_OPENSHELL_VERSION:
        raise ProvisionError("provider manifest OpenShell version is not pinned correctly")
    if (
        manifest["gateway"] != EXPECTED_GATEWAY
        or manifest["gateway_endpoint"] != EXPECTED_GATEWAY_ENDPOINT
        or manifest["gateway_type"] != EXPECTED_GATEWAY_TYPE
        or manifest["gateway_auth"] != EXPECTED_GATEWAY_AUTH
        or manifest["providers_v2_required"] is not True
    ):
        raise ProvisionError("provider manifest gateway contract changed")
    if manifest["request_body_credential_rewrite_max_bytes"] != TAVILY_BODY_REWRITE_MAX_BYTES:
        raise ProvisionError("Tavily request-body rewrite boundary must remain 256 KiB")

    upstream = _load_json(UPSTREAM_VERSION_PATH, label="OpenShell upstream version record")
    if upstream.get("version") != f"v{PINNED_OPENSHELL_VERSION}":
        raise ProvisionError("provider assets and pinned OpenShell release disagree")

    raw_specs = manifest["providers"]
    if not isinstance(raw_specs, list) or not raw_specs:
        raise ProvisionError("provider manifest requires provider entries")
    all_specs: list[ProviderSpec] = []
    seen_names: set[str] = set()
    seen_profile_ids: set[str] = set()
    profiles: dict[str, Mapping[str, Any]] = {}
    canonical_specs: list[Mapping[str, Any]] = []
    for raw in raw_specs:
        if not isinstance(raw, dict):
            raise ProvisionError("provider manifest entry must be an object")
        _require_keys(
            raw,
            required={"name", "profile_id", "profile", "credential_keys"},
            allowed={"name", "profile_id", "profile", "credential_keys"},
            label="provider manifest entry",
        )
        name = raw["name"]
        profile_id = raw["profile_id"]
        keys = raw["credential_keys"]
        if not isinstance(name, str) or not PROFILE_ID_RE.fullmatch(name) or name in seen_names:
            raise ProvisionError("provider instance names must be unique and stable")
        if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id) or profile_id in seen_profile_ids:
            raise ProvisionError("provider profile ids must be unique and stable")
        if (
            not isinstance(keys, list)
            or not keys
            or not all(isinstance(key, str) and ENV_NAME_RE.fullmatch(key) for key in keys)
            or len(set(keys)) != len(keys)
        ):
            raise ProvisionError("provider credential key list is invalid")
        profile_path = _asset_path(raw["profile"], suffixes={".yaml", ".yml"})
        spec = ProviderSpec(name, profile_id, profile_path, tuple(keys))
        profile = _load_profile(profile_path)
        _validate_profile(profile, spec)
        all_specs.append(spec)
        profiles[profile_id] = profile
        canonical_specs.append(copy.deepcopy(raw))
        seen_names.add(name)
        seen_profile_ids.add(profile_id)

    auth_path = _asset_path(manifest["hermes_auth_template"], suffixes={".json"})
    auth_template = _load_json(auth_path, label="Hermes auth template")
    _validate_hermes_auth_template(auth_template, all_specs)

    requested = list(selected_names or [])
    unknown = set(requested) - seen_names
    if unknown:
        raise ProvisionError("an unknown provider was requested")
    requested_set = set(requested)
    selected_specs = tuple(spec for spec in all_specs if not requested_set or spec.name in requested_set)
    if not selected_specs:
        raise ProvisionError("at least one provider must be selected")

    selected_names_set = {spec.name for spec in selected_specs}
    summary_value = {
        "schema_version": manifest["schema_version"],
        "openshell_version": manifest["openshell_version"],
        "gateway": manifest["gateway"],
        "gateway_endpoint": manifest["gateway_endpoint"],
        "gateway_type": manifest["gateway_type"],
        "gateway_auth": manifest["gateway_auth"],
        "providers_v2_required": manifest["providers_v2_required"],
        "request_body_credential_rewrite_max_bytes": manifest["request_body_credential_rewrite_max_bytes"],
        "providers": [raw for raw in canonical_specs if raw["name"] in selected_names_set],
        "profiles": {spec.profile_id: profiles[spec.profile_id] for spec in selected_specs},
        "hermes_auth_template": (auth_template if MINIMAX_PROVIDER_NAME in selected_names_set else None),
        "upstream_commit": upstream.get("commit"),
    }
    return ProvisionPlan(
        manifest=manifest,
        specs=selected_specs,
        profiles=profiles,
        hermes_auth_template=auth_template,
        summary_sha256=_canonical_sha256(summary_value),
    )


def _minimal_child_environment(credentials: Mapping[str, str]) -> dict[str, str]:
    account = pwd.getpwuid(os.geteuid())
    environment = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TERM": "dumb",
        "NO_COLOR": "1",
    }
    for key, value in credentials.items():
        if not ENV_NAME_RE.fullmatch(key):
            raise ProvisionError("refusing an invalid child credential environment key")
        _validate_secret_value(value)
        environment[key] = value
    return environment


@contextmanager
def _maintenance_lock():
    """Exclude sandbox lifecycle changes for the full provisioning window."""
    state_root = PROJECT_ROOT / "var" / "openshell"
    lock_directory = MAINTENANCE_LOCK_PATH.parent
    expected_state_root = PROJECT_ROOT.resolve(strict=True) / "var" / "openshell"
    if not state_root.is_dir() or state_root.is_symlink() or state_root.resolve(strict=True) != expected_state_root:
        raise ProvisionError("OpenShell state root is unavailable or unsafe")
    metadata = state_root.stat()
    if metadata.st_uid != os.geteuid() or metadata.st_mode & 0o077:
        raise ProvisionError("OpenShell state root ownership or permissions are unsafe")
    try:
        lock_directory.mkdir(mode=0o700, exist_ok=True)
    except OSError as exc:
        raise ProvisionError("OpenShell maintenance lock directory is unavailable") from exc
    if lock_directory.is_symlink() or not lock_directory.is_dir():
        raise ProvisionError("OpenShell maintenance lock directory is unsafe")
    directory_metadata = lock_directory.stat()
    if directory_metadata.st_uid != os.geteuid() or directory_metadata.st_mode & 0o077:
        raise ProvisionError("OpenShell maintenance lock directory permissions are unsafe")

    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(MAINTENANCE_LOCK_PATH, flags, 0o600)
    except OSError as exc:
        raise ProvisionError("OpenShell maintenance lock is unavailable") from exc
    try:
        lock_metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(lock_metadata.st_mode)
            or lock_metadata.st_uid != os.geteuid()
            or lock_metadata.st_nlink != 1
        ):
            raise ProvisionError("OpenShell maintenance lock is unsafe")
        os.fchmod(descriptor, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProvisionError("another SIQ OpenShell lifecycle operation is in progress") from exc
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _read_restricted_file(path: Path) -> str:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(os.fspath(path), flags)
    except OSError as exc:
        raise ProvisionError("secret source failed security validation") from exc
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & 0o077
            or metadata.st_nlink != 1
            or metadata.st_size <= 0
            or metadata.st_size > MAX_SECRET_FILE_BYTES
        ):
            raise ProvisionError("secret source failed security validation")
        chunks: list[bytes] = []
        remaining = metadata.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) != metadata.st_size:
            raise ProvisionError("secret source changed while being read")
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProvisionError("secret source must be UTF-8 text") from exc
    finally:
        os.close(descriptor)


def _validate_secret_value(value: str) -> None:
    if not isinstance(value, str):
        raise ProvisionError("credential value has an invalid type")
    encoded = value.encode("utf-8")
    if (
        not value.strip()
        or len(encoded) > MAX_SECRET_VALUE_BYTES
        or "\x00" in value
        or "\n" in value
        or "\r" in value
        or value.startswith("openshell:resolve:")
        or value.strip().lower() in {"changeme", "placeholder", "replace-me"}
    ):
        raise ProvisionError("credential value failed validation")


def _merge_secret(target: dict[str, str], key: str, value: str) -> None:
    _validate_secret_value(value)
    existing = target.get(key)
    if existing is not None and not hmac.compare_digest(existing.encode("utf-8"), value.encode("utf-8")):
        raise ProvisionError(f"credential {key} conflicts across secret sources")
    target[key] = value


def _parse_dotenv(path: Path, expected_keys: set[str]) -> dict[str, str]:
    text = _read_restricted_file(path)
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise ProvisionError("secret dotenv source contains a non-assignment line")
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise ProvisionError("secret dotenv source contains an invalid key")
        value = raw_value.strip()
        if value[:1] in {"'", '"'}:
            if len(value) < 2 or value[-1] != value[0]:
                raise ProvisionError("secret dotenv source contains an unmatched quote")
            value = value[1:-1]
        if key in expected_keys:
            if key in result:
                raise ProvisionError(f"credential {key} appears twice in one secret source")
            _validate_secret_value(value)
            result[key] = value
    return result


def _parse_minimax_auth_json(path: Path) -> dict[str, str]:
    try:
        document = json.loads(_read_restricted_file(path))
    except json.JSONDecodeError as exc:
        raise ProvisionError("Hermes auth secret source is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ProvisionError("Hermes auth secret source has an invalid structure")
    pool_map = document.get("credential_pool")
    pool = pool_map.get("minimax-cn") if isinstance(pool_map, dict) else None
    if not isinstance(pool, list) or len(pool) != 2:
        raise ProvisionError("Hermes auth source must contain exactly two MiniMax China entries")
    try:
        ordered = sorted(pool, key=lambda item: (item["priority"], item["id"]))
    except (KeyError, TypeError) as exc:
        raise ProvisionError("Hermes MiniMax source entries are incomplete") from exc
    expected = (
        ("minimax_cn_primary_0", 0, "SIQ_MINIMAX_CN_PRIMARY"),
        ("minimax_cn_backup_10", 10, "SIQ_MINIMAX_CN_BACKUP"),
    )
    result: dict[str, str] = {}
    for entry, (expected_id, priority, env_key) in zip(ordered, expected, strict=True):
        if (
            not isinstance(entry, dict)
            or entry.get("id") != expected_id
            or entry.get("priority") != priority
            or entry.get("base_url") != "https://api.minimax.chat/v1"
        ):
            raise ProvisionError("Hermes MiniMax source differs from the reviewed pool")
        value = entry.get("access_token")
        _validate_secret_value(value)
        result[env_key] = value
    return result


def load_secrets(
    plan: ProvisionPlan,
    *,
    secret_files: Sequence[Path],
    minimax_auth_json: Path | None,
    environment: Mapping[str, str],
) -> dict[str, str]:
    expected = {key for spec in plan.specs for key in spec.credential_keys}
    values: dict[str, str] = {}
    for key in sorted(expected):
        if key in environment:
            _merge_secret(values, key, environment[key])
    for path in secret_files:
        for key, value in _parse_dotenv(path, expected).items():
            _merge_secret(values, key, value)
    if minimax_auth_json is not None:
        if MINIMAX_PROVIDER_NAME not in plan.provider_names:
            raise ProvisionError("MiniMax auth source was supplied without selecting its provider")
        for key, value in _parse_minimax_auth_json(minimax_auth_json).items():
            _merge_secret(values, key, value)
    missing = expected - set(values)
    if missing:
        raise ProvisionError("required provider credentials are missing")
    if MINIMAX_PROVIDER_NAME in plan.provider_names:
        primary = values["SIQ_MINIMAX_CN_PRIMARY"].encode("utf-8")
        backup = values["SIQ_MINIMAX_CN_BACKUP"].encode("utf-8")
        if hmac.compare_digest(primary, backup):
            raise ProvisionError("MiniMax primary and backup credentials must be distinct")
    return values


def _checked(
    runner: Runner,
    arguments: Sequence[str],
    *,
    operation: str,
    credential_env: Mapping[str, str] | None = None,
) -> CliResult:
    try:
        result = runner.run(arguments, credential_env=credential_env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ProvisionError(f"{operation} could not execute") from exc
    if result.returncode != 0:
        raise ProvisionError(f"{operation} failed with exit {result.returncode}; child output suppressed")
    return result


def _parse_json_output(result: CliResult, *, operation: str) -> Any:
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProvisionError(f"{operation} returned invalid structured output") from exc


def _prune_empty(value: Any) -> Any:
    if isinstance(value, dict):
        pruned: dict[str, Any] = {}
        for key, item in value.items():
            if key == "resource_version":
                continue
            normalized = _prune_empty(item)
            if normalized in (None, "", [], {}):
                continue
            pruned[key] = normalized
        return pruned
    if isinstance(value, list):
        return [_prune_empty(item) for item in value]
    return value


def _require_cli_contract(runner: Runner) -> None:
    version = _checked(runner, ["--version"], operation="OpenShell version check")
    match = re.search(r"(?:^|\s)(\d+\.\d+\.\d+)(?:\s|$)", version.stdout.strip())
    if not match or match.group(1) != PINNED_OPENSHELL_VERSION:
        raise ProvisionError("project OpenShell CLI does not match pinned version 0.0.83")
    gateways_result = _checked(
        runner,
        ["gateway", "list", "-o", "json"],
        operation="OpenShell gateway registration check",
    )
    gateways = _parse_json_output(gateways_result, operation="OpenShell gateway registration check")
    if not isinstance(gateways, list):
        raise ProvisionError("OpenShell gateway registration list has an unexpected shape")
    matches = [item for item in gateways if isinstance(item, dict) and item.get("name") == EXPECTED_GATEWAY]
    if len(matches) != 1:
        raise ProvisionError("isolated SIQ gateway registration is missing or ambiguous")
    gateway = matches[0]
    if (
        gateway.get("active") is not True
        or gateway.get("endpoint") != EXPECTED_GATEWAY_ENDPOINT
        or gateway.get("type") != EXPECTED_GATEWAY_TYPE
        or gateway.get("source") != "user"
        or gateway.get("auth") != EXPECTED_GATEWAY_AUTH
    ):
        raise ProvisionError("isolated SIQ gateway registration does not match the pinned local mTLS target")
    settings_result = _checked(
        runner,
        ["settings", "get", "--global", "--json"],
        operation="OpenShell provider-v2 setting check",
    )
    settings = _parse_json_output(settings_result, operation="OpenShell settings query")
    configured = settings.get("settings", {}).get("providers_v2_enabled") if isinstance(settings, dict) else None
    if str(configured).lower() != "true":
        raise ProvisionError("gateway providers_v2_enabled must be true before provisioning")


def _require_quiescent_gateway(runner: Runner) -> None:
    listed = _checked(
        runner,
        ["sandbox", "list", "--limit", "1", "-o", "json"],
        operation="OpenShell sandbox quiescence check",
    )
    sandboxes = _parse_json_output(listed, operation="OpenShell sandbox quiescence check")
    if not isinstance(sandboxes, list):
        raise ProvisionError("OpenShell sandbox list has an unexpected shape")
    if sandboxes:
        raise ProvisionError("provider provisioning requires a gateway with no sandboxes")


def _profile_actions(plan: ProvisionPlan, runner: Runner) -> list[tuple[str, ProviderSpec, int]]:
    listed = _checked(
        runner,
        ["provider", "list-profiles", "-o", "json"],
        operation="OpenShell provider profile list",
    )
    values = _parse_json_output(listed, operation="OpenShell provider profile list")
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ProvisionError("OpenShell provider profile list has an unexpected shape")
    existing_ids = {item.get("id") for item in values}
    actions: list[tuple[str, ProviderSpec, int]] = []
    for spec in plan.specs:
        if spec.profile_id not in existing_ids:
            # OpenShell's lint command consults the gateway registry and rejects
            # already-registered IDs. Lint only profiles that will be imported;
            # existing profiles are exported and compared in full below.
            _checked(
                runner,
                ["provider", "profile", "lint", "--file", str(spec.profile_path)],
                operation=f"OpenShell provider profile lint for {spec.profile_id}",
            )
            actions.append(("import", spec, 0))
            continue
        exported = _checked(
            runner,
            ["provider", "profile", "export", spec.profile_id, "-o", "json"],
            operation=f"provider profile export for {spec.profile_id}",
        )
        current = _parse_json_output(exported, operation="OpenShell provider profile export")
        if not isinstance(current, dict):
            raise ProvisionError("OpenShell provider profile export has an unexpected shape")
        resource_version = current.get("resource_version")
        if not isinstance(resource_version, int) or resource_version <= 0:
            raise ProvisionError("existing custom provider profile lacks a resource version")
        desired = plan.profiles[spec.profile_id]
        if _prune_empty(current) == _prune_empty(desired):
            actions.append(("unchanged", spec, resource_version))
        else:
            actions.append(("update", spec, resource_version))
    return actions


def _provider_actions(plan: ProvisionPlan, runner: Runner) -> list[tuple[str, ProviderSpec]]:
    listed = _checked(
        runner,
        ["provider", "list", "--limit", "1000", "-o", "json"],
        operation="OpenShell provider list",
    )
    values = _parse_json_output(listed, operation="OpenShell provider list")
    if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
        raise ProvisionError("OpenShell provider list has an unexpected shape")
    existing = {item.get("name"): item for item in values if isinstance(item.get("name"), str)}
    actions: list[tuple[str, ProviderSpec]] = []
    for spec in plan.specs:
        item = existing.get(spec.name)
        if item is None:
            actions.append(("create", spec))
        elif item.get("type") == spec.profile_id:
            credential_keys = item.get("credential_keys")
            config_keys = item.get("config_keys", [])
            if not isinstance(credential_keys, list) or not all(isinstance(key, str) for key in credential_keys):
                raise ProvisionError(f"provider {spec.name} has invalid credential metadata")
            if set(credential_keys) - set(spec.credential_keys):
                raise ProvisionError(f"provider {spec.name} has unreviewed credential keys")
            if not isinstance(config_keys, list) or config_keys:
                raise ProvisionError(f"provider {spec.name} has unreviewed config keys")
            actions.append(("update", spec))
        else:
            raise ProvisionError(f"provider {spec.name} exists with an unexpected type")
    return actions


def _apply_profile_actions(
    plan: ProvisionPlan,
    actions: Sequence[tuple[str, ProviderSpec, int]],
    runner: Runner,
) -> None:
    for action, spec, resource_version in actions:
        if action == "unchanged":
            continue
        if action == "import":
            _checked(
                runner,
                ["provider", "profile", "import", "--file", str(spec.profile_path)],
                operation=f"provider profile import for {spec.profile_id}",
            )
            continue
        if action != "update":
            raise ProvisionError("internal provider profile action is invalid")
        document = copy.deepcopy(plan.profiles[spec.profile_id])
        document["resource_version"] = resource_version
        with tempfile.TemporaryDirectory(prefix="siq-openshell-provider-") as directory:
            update_path = Path(directory) / f"{spec.profile_id}.json"
            descriptor = os.open(update_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    json.dump(document, stream, ensure_ascii=True, sort_keys=True)
                    stream.write("\n")
            except BaseException:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            _checked(
                runner,
                [
                    "provider",
                    "profile",
                    "update",
                    spec.profile_id,
                    "--file",
                    str(update_path),
                ],
                operation=f"provider profile update for {spec.profile_id}",
            )


def provider_credential_command(action: str, spec: ProviderSpec) -> list[str]:
    if action == "create":
        command = [
            "provider",
            "create",
            "--name",
            spec.name,
            "--type",
            spec.profile_id,
        ]
    elif action == "update":
        command = ["provider", "update", spec.name]
    else:
        raise ProvisionError("internal provider action is invalid")
    for key in spec.credential_keys:
        command.extend(["--credential", key])
    return command


def apply_provider_actions(
    actions: Sequence[tuple[str, ProviderSpec]],
    secrets: Mapping[str, str],
    runner: Runner,
) -> None:
    for action, spec in actions:
        scoped_secrets = {key: secrets[key] for key in spec.credential_keys}
        command = provider_credential_command(action, spec)
        if any(value in command for value in scoped_secrets.values()):
            raise ProvisionError("refusing to place a credential value in CLI arguments")
        _checked(
            runner,
            command,
            operation=f"provider credential {action} for {spec.name}",
            credential_env=scoped_secrets,
        )


def _verify_providers(plan: ProvisionPlan, runner: Runner) -> None:
    listed = _checked(
        runner,
        ["provider", "list", "--limit", "1000", "-o", "json"],
        operation="OpenShell provider verification",
    )
    values = _parse_json_output(listed, operation="OpenShell provider verification")
    if not isinstance(values, list):
        raise ProvisionError("OpenShell provider verification has an unexpected shape")
    existing = {item.get("name"): item for item in values if isinstance(item, dict)}
    for spec in plan.specs:
        item = existing.get(spec.name)
        keys = item.get("credential_keys") if isinstance(item, dict) else None
        if (
            not isinstance(item, dict)
            or item.get("type") != spec.profile_id
            or not isinstance(keys, list)
            or set(keys) != set(spec.credential_keys)
        ):
            raise ProvisionError(f"provider verification failed for {spec.name}")


def _result_document(plan: ProvisionPlan, *, status: str | None = None) -> str:
    document: dict[str, Any] = {
        "providers": plan.provider_names,
        "summary_sha256": plan.summary_sha256,
    }
    if status is not None:
        document["status"] = status
    return json.dumps(document, ensure_ascii=True, sort_keys=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate SIQ provider assets; provisioning requires explicit --apply."
    )
    parser.add_argument(
        "--provider",
        action="append",
        default=[],
        metavar="NAME",
        help="Provision only this manifest provider; repeat for multiple providers.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Import/update provider profiles and create/update provider credentials.",
    )
    parser.add_argument(
        "--confirm-gateway",
        metavar="NAME",
        help=f"Required with --apply; must be {EXPECTED_GATEWAY}.",
    )
    parser.add_argument(
        "--secret-file",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="Restricted dotenv file; values are never placed in argv or output.",
    )
    parser.add_argument(
        "--minimax-auth-json",
        type=Path,
        metavar="PATH",
        help="Restricted Hermes auth.json containing the reviewed two-entry pool.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan = load_plan(args.provider)
        if not args.apply:
            if args.confirm_gateway or args.secret_file or args.minimax_auth_json:
                raise ProvisionError("secret source and gateway options require --apply")
            print(_result_document(plan))
            return 0
        if args.confirm_gateway != EXPECTED_GATEWAY:
            raise ProvisionError(f"--apply requires --confirm-gateway {EXPECTED_GATEWAY}")

        # Hold the lifecycle lock from identity/quiescence checks through verification.
        runner = SubprocessRunner()
        with _maintenance_lock():
            _require_cli_contract(runner)
            _require_quiescent_gateway(runner)
            profile_actions = _profile_actions(plan, runner)
            provider_actions = _provider_actions(plan, runner)

            # Secret files are opened only after every read-only preflight succeeds.
            secret_values = load_secrets(
                plan,
                secret_files=args.secret_file,
                minimax_auth_json=args.minimax_auth_json,
                environment=os.environ,
            )
            _apply_profile_actions(plan, profile_actions, runner)
            apply_provider_actions(provider_actions, secret_values, runner)
            _verify_providers(plan, runner)
        print(_result_document(plan, status="provisioned"))
        return 0
    except ProvisionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("error: OpenShell provider operation timed out; child output suppressed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
