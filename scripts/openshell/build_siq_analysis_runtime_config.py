#!/usr/bin/env python3
"""Compile the live siq_analysis config into a secret-free sandbox config."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

import yaml

SCHEMA_VERSION = "siq.openshell.hermes_runtime_config.v1"
PROFILE = "siq_analysis"
DEFAULT_PROJECT_ROOT = "/home/maoyd/siq-research-engine"
DEFAULT_HOST_ALIAS = "host.openshell.internal"
DEFAULT_API_PORT = 28651
LOCAL_SERVICE_PORTS = {8004, 8006, 8007, 8013}
EXPECTED_TOOLSETS = ["terminal", "file", "code_execution", "web"]
INLINE_SECRET_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "password",
    "private_key",
    "secret",
    "token",
}
SECRET_ENV_RE = re.compile(r"(?:API_KEY|AUTHORIZATION|COOKIE|CREDENTIAL|PASSWORD|PRIVATE_KEY|SECRET|TOKEN)")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,127}$")
PROVIDER_PLACEHOLDER_ENVS = {
    "EXA_API_KEY",
    "KIMI_API_KEY",
    "SIQ_MINIMAX_CN_BACKUP",
    "SIQ_MINIMAX_CN_PRIMARY",
    "SIQ_STEPFUN_LLM_API_KEY",
    "TAVILY_API_KEY",
}
BROKER_RUNTIME_ENVS = ("SIQ_PG_QUERY_BROKER_URL",)


class RuntimeConfigError(RuntimeError):
    pass


def _sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _load_yaml(path: Path) -> tuple[dict[str, Any], bytes]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeConfigError("runtime config must be a regular, non-symlink file")
    content = path.read_bytes()
    try:
        payload = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise RuntimeConfigError("runtime config is not valid YAML") from exc
    if not isinstance(payload, dict):
        raise RuntimeConfigError("runtime config must contain a mapping")
    return payload, content


def _validate_no_inline_secrets(value: Any, path: tuple[str, ...] = ()) -> list[str]:
    key_envs: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in value.items():
            key = str(raw_key)
            lowered = key.lower()
            child_path = (*path, key)
            if lowered in INLINE_SECRET_KEYS and child not in (None, "", [], {}):
                raise RuntimeConfigError(f"inline secret value is forbidden at {'.'.join(child_path)}")
            if lowered == "key_env":
                if not isinstance(child, str) or not ENV_NAME_RE.fullmatch(child):
                    raise RuntimeConfigError(f"invalid key_env at {'.'.join(child_path)}")
                key_envs.append(child)
            key_envs.extend(_validate_no_inline_secrets(child, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            key_envs.extend(_validate_no_inline_secrets(child, (*path, str(index))))
    return key_envs


def _rewrite_local_base_urls(value: Any, *, host_alias: str) -> list[dict[str, Any]]:
    rewrites: list[dict[str, Any]] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "base_url" and isinstance(child, str) and child.strip():
                parsed = urlsplit(child.strip())
                if parsed.username or parsed.password or parsed.fragment:
                    raise RuntimeConfigError("base_url must not contain credentials or fragments")
                if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
                    if parsed.scheme != "http" or parsed.port not in LOCAL_SERVICE_PORTS:
                        raise RuntimeConfigError(f"unsupported loopback service URL: {child}")
                    value[key] = urlunsplit(
                        (parsed.scheme, f"{host_alias}:{parsed.port}", parsed.path, parsed.query, "")
                    )
                    rewrites.append({"port": parsed.port, "target_host": host_alias})
            rewrites.extend(_rewrite_local_base_urls(child, host_alias=host_alias))
    elif isinstance(value, list):
        for child in value:
            rewrites.extend(_rewrite_local_base_urls(child, host_alias=host_alias))
    return rewrites


def _route_entry(value: Mapping[str, Any]) -> dict[str, str]:
    provider = str(value.get("provider") or "").strip()
    model = str(value.get("default") or value.get("model") or "").strip()
    if not provider or not model:
        raise RuntimeConfigError("every model route requires provider and model")
    base_url = str(value.get("base_url") or "").strip()
    host = urlsplit(base_url).hostname or "provider-default"
    return {"provider": provider, "model": model, "host": host}


def compile_runtime_config(
    payload: Mapping[str, Any],
    *,
    source_sha256: str,
    project_root: str = DEFAULT_PROJECT_ROOT,
    host_alias: str = DEFAULT_HOST_ALIAS,
    api_port: int = DEFAULT_API_PORT,
) -> tuple[bytes, bytes, dict[str, Any]]:
    if not project_root.startswith("/") or ".." in Path(project_root).parts:
        raise RuntimeConfigError("project root must be an absolute normalized path")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,252}", host_alias):
        raise RuntimeConfigError("host alias is invalid")
    if not 1 <= api_port <= 65535:
        raise RuntimeConfigError("API port is invalid")

    compiled = copy.deepcopy(dict(payload))
    key_envs = sorted(set(_validate_no_inline_secrets(compiled)))

    model = compiled.get("model")
    fallbacks = compiled.get("fallback_providers")
    if not isinstance(model, dict) or not isinstance(fallbacks, list) or not fallbacks:
        raise RuntimeConfigError("model and non-empty fallback_providers are required")
    if not all(isinstance(item, dict) for item in fallbacks):
        raise RuntimeConfigError("fallback provider entries must be mappings")
    source_routes = [_route_entry(model), *[_route_entry(item) for item in fallbacks]]
    route_pairs = [(item["provider"], item["model"]) for item in source_routes]
    if len(route_pairs) != len(set(route_pairs)):
        raise RuntimeConfigError("model route order contains duplicate provider/model pairs")

    if compiled.get("toolsets") != EXPECTED_TOOLSETS:
        raise RuntimeConfigError("siq_analysis toolsets differ from the reviewed contract")
    rewrites = _rewrite_local_base_urls(compiled, host_alias=host_alias)
    compiled_model = compiled["model"]
    compiled_fallbacks = compiled["fallback_providers"]
    routes = [_route_entry(compiled_model), *[_route_entry(item) for item in compiled_fallbacks]]
    unique_rewrites = [
        {"port": port, "target_host": target_host}
        for port, target_host in sorted({(item["port"], item["target_host"]) for item in rewrites})
    ]

    terminal = compiled.get("terminal")
    if not isinstance(terminal, dict):
        raise RuntimeConfigError("terminal configuration is missing")
    original_passthrough = terminal.get("env_passthrough")
    if not isinstance(original_passthrough, list) or not all(isinstance(item, str) for item in original_passthrough):
        raise RuntimeConfigError("terminal env_passthrough must be a string list")
    stripped_terminal_env = sorted(
        {name for name in original_passthrough if SECRET_ENV_RE.search(name) and name not in PROVIDER_PLACEHOLDER_ENVS}
    )
    terminal_env = [name for name in original_passthrough if name not in stripped_terminal_env]
    for name in sorted(PROVIDER_PLACEHOLDER_ENVS | set(BROKER_RUNTIME_ENVS)):
        if name not in terminal_env:
            terminal_env.append(name)
    terminal["env_passthrough"] = terminal_env
    terminal["backend"] = "local"
    terminal["cwd"] = project_root
    terminal["shell_init_files"] = []
    terminal["auto_source_bashrc"] = False

    security = compiled.get("security")
    if not isinstance(security, dict):
        raise RuntimeConfigError("security configuration is missing")
    security["redact_secrets"] = True

    browser = compiled.get("browser")
    if isinstance(browser, dict):
        browser["allow_private_urls"] = False

    platforms = compiled.get("platforms")
    if not isinstance(platforms, dict) or not isinstance(platforms.get("api_server"), dict):
        raise RuntimeConfigError("api_server platform configuration is missing")
    api_server = platforms["api_server"]
    api_server["enabled"] = True
    if api_server.get("key") not in (None, ""):
        raise RuntimeConfigError("api_server key must be injected at runtime, not stored in config")
    api_server["key"] = ""
    extra = api_server.get("extra")
    if not isinstance(extra, dict):
        extra = {}
        api_server["extra"] = extra
    extra.update({"host": "127.0.0.1", "port": api_port, "model_name": PROFILE})

    content = yaml.safe_dump(
        compiled,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=120,
    ).encode("utf-8")
    output_sha256 = _sha256_bytes(content)
    summary = {
        "schema_version": SCHEMA_VERSION,
        "profile": PROFILE,
        "source_sha256": source_sha256,
        "output_sha256": output_sha256,
        "project_root": project_root,
        "api": {"host": "127.0.0.1", "port": api_port},
        "source_routes": source_routes,
        "routes": routes,
        "route_order_preserved": True,
        "loopback_rewrites": unique_rewrites,
        "loopback_rewrite_occurrences": len(rewrites),
        "credential_env_names": key_envs,
        "terminal_env_removed": stripped_terminal_env,
        "terminal_provider_placeholder_env": sorted(PROVIDER_PLACEHOLDER_ENVS),
        "terminal_broker_env": list(BROKER_RUNTIME_ENVS),
        "inline_secret_values": False,
    }
    summary_content = (json.dumps(summary, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return content, summary_content, summary


def _write_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise RuntimeConfigError(f"refusing symlink output: {path}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        Path(temporary).unlink(missing_ok=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--host-alias", default=DEFAULT_HOST_ALIAS)
    parser.add_argument("--api-port", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        payload, source_content = _load_yaml(args.input)
        content, summary_content, summary = compile_runtime_config(
            payload,
            source_sha256=_sha256_bytes(source_content),
            project_root=args.project_root,
            host_alias=args.host_alias,
            api_port=args.api_port,
        )
        if args.check:
            if not args.output.is_file() or args.output.is_symlink() or args.output.read_bytes() != content:
                raise RuntimeConfigError("compiled runtime config is missing or stale")
            if (
                not args.summary_output.is_file()
                or args.summary_output.is_symlink()
                or args.summary_output.read_bytes() != summary_content
            ):
                raise RuntimeConfigError("compiled runtime summary is missing or stale")
        else:
            _write_atomic(args.output, content)
            _write_atomic(args.summary_output, summary_content)
        print(json.dumps(summary, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, RuntimeConfigError) as exc:
        print(f"siq_analysis runtime config compile failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
