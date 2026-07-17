#!/usr/bin/env python3
"""Render and validate the isolated SIQ OpenShell gateway configuration."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import tomllib
from pathlib import Path
from typing import Any, Mapping

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from gateway_bind_contract import (  # noqa: E402
    CONTRACT,
    GATEWAY_NAME,
    VERSION,
    BindContractError,
    load_activation_record,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
GATEWAY_PORT = 17671
HEALTH_PORT = 17672


class GatewayConfigError(RuntimeError):
    pass


def _table(payload: Mapping[str, Any], *keys: str) -> Mapping[str, Any]:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict) or not isinstance(current.get(key), dict):
            raise GatewayConfigError(f"missing gateway config table: {'.'.join(keys)}")
        current = current[key]
    return current


def validate_config(
    content: bytes,
    *,
    project_root: Path,
    bind_mounts_enabled: bool = False,
) -> dict[str, Any]:
    try:
        text = content.decode("utf-8")
        payload = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise GatewayConfigError("generated gateway config is invalid TOML") from exc
    if "${" in text:
        raise GatewayConfigError("generated gateway config contains unresolved tokens")
    openshell = _table(payload, "openshell")
    gateway = _table(payload, "openshell", "gateway")
    tls = _table(payload, "openshell", "gateway", "tls")
    auth = _table(payload, "openshell", "gateway", "auth")
    mtls = _table(payload, "openshell", "gateway", "mtls_auth")
    docker = _table(payload, "openshell", "drivers", "docker")
    if openshell.get("version") != 1:
        raise GatewayConfigError("gateway config schema version must be 1")
    expected_gateway = {
        "bind_address": f"127.0.0.1:{GATEWAY_PORT}",
        "health_bind_address": f"127.0.0.1:{HEALTH_PORT}",
        "compute_drivers": ["docker"],
        "sandbox_namespace": GATEWAY_NAME,
        "enable_loopback_service_http": False,
    }
    if any(gateway.get(key) != value for key, value in expected_gateway.items()):
        raise GatewayConfigError("gateway listener or driver boundary changed")
    if tls.get("require_client_auth") is not True or mtls.get("enabled") is not True:
        raise GatewayConfigError("gateway mTLS must remain enabled")
    if auth.get("allow_unauthenticated_users") is not False:
        raise GatewayConfigError("unauthenticated gateway access is forbidden")
    root = project_root.resolve(strict=True)
    if bind_mounts_enabled:
        expected_bind_config = {
            "enable_bind_mounts": True,
            "bind_mount_contract": CONTRACT,
            "bind_mount_project_root": str(root),
        }
        if any(docker.get(key) != value for key, value in expected_bind_config.items()):
            raise GatewayConfigError("Docker bind-mount contract is not the exact attested SIQ contract")
    else:
        if docker.get("enable_bind_mounts") is not False:
            raise GatewayConfigError("Docker host bind mounts must remain disabled without activation")
        if "bind_mount_contract" in docker or "bind_mount_project_root" in docker:
            raise GatewayConfigError("disabled Docker bind mounts must not retain contract fields")
    if docker.get("sandbox_namespace") != GATEWAY_NAME or docker.get("network_name") != GATEWAY_NAME:
        raise GatewayConfigError("Docker sandbox namespace must remain isolated")
    if docker.get("default_image") != f"ghcr.io/nvidia/openshell/sandbox:{VERSION}":
        raise GatewayConfigError("sandbox image must remain version-pinned")
    for key in ("cert_path", "key_path", "client_ca_path"):
        path = Path(str(tls.get(key) or ""))
        if not path.is_absolute():
            raise GatewayConfigError(f"gateway TLS path must be absolute: {key}")
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise GatewayConfigError(f"gateway TLS path escaped the project: {key}") from exc
    supervisor = Path(str(docker.get("supervisor_bin") or ""))
    expected_supervisor = root / f"var/openshell/toolchains/v{VERSION}/bin/openshell-sandbox"
    if supervisor != expected_supervisor:
        raise GatewayConfigError("sandbox supervisor path is not the pinned project binary")
    return payload


def render_config(
    template: bytes,
    *,
    project_root: Path,
    activation: Mapping[str, str] | None = None,
) -> bytes:
    root = project_root.resolve(strict=True)
    bind_mount_config = "enable_bind_mounts = false"
    if activation is not None:
        if activation.get("contract") != CONTRACT or activation.get("project_root") != str(root):
            raise GatewayConfigError("bind-contract activation does not match this project")
        bind_mount_config = "\n".join(
            (
                "enable_bind_mounts = true",
                f'bind_mount_contract = "{CONTRACT}"',
                f'bind_mount_project_root = "{root}"',
            )
        )
    replacements = {
        b"${SIQ_OPENSHELL_TLS_ROOT}": str(root / f"var/openshell/gateway/{GATEWAY_NAME}/tls").encode(),
        b"${SIQ_OPENSHELL_BIN_ROOT}": str(root / f"var/openshell/toolchains/v{VERSION}/bin").encode(),
        b"${SIQ_OPENSHELL_BIND_MOUNT_CONFIG}": bind_mount_config.encode(),
    }
    content = template
    for token, value in replacements.items():
        content = content.replace(token, value)
    validate_config(content, project_root=root, bind_mounts_enabled=activation is not None)
    return content


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--template",
        type=Path,
        default=Path("infra/openshell/gateway/siq-openshell-dev.toml.template"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("var/openshell/gateway/siq-openshell-dev/gateway.toml"),
    )
    parser.add_argument(
        "--activation-record",
        type=Path,
        default=Path(f"var/openshell/gateway/{GATEWAY_NAME}/bind-contract.activation.json"),
    )
    parser.add_argument("--check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = args.project_root.resolve(strict=True)
    template_path = args.template if args.template.is_absolute() else root / args.template
    output_path = args.output if args.output.is_absolute() else root / args.output
    try:
        template_path.resolve(strict=True).relative_to(root)
        output_path.resolve(strict=False).relative_to(root)
        activation_path = (
            args.activation_record if args.activation_record.is_absolute() else root / args.activation_record
        )
        activation = load_activation_record(
            activation_path,
            project_root=root,
            required=False,
        )
        content = render_config(
            template_path.read_bytes(),
            project_root=root,
            activation=activation,
        )
        existing = output_path.read_bytes() if output_path.is_file() and not output_path.is_symlink() else b""
        if args.check:
            return 0 if existing == content else 1
        _atomic_write(output_path, content)
        print(f"gateway config: {GATEWAY_NAME} {GATEWAY_PORT}/{HEALTH_PORT}")
        return 0
    except (OSError, ValueError, BindContractError, GatewayConfigError) as exc:
        print(f"gateway config error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
