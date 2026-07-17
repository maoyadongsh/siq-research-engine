#!/usr/bin/env python3
"""Discover the one Docker bridge endpoint trusted by SIQ OpenShell brokers."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

SCHEMA_VERSION = "siq.openshell.bridge-endpoint.v1"
NETWORK_NAME = "siq-openshell-dev"
HOST_ALIAS = "host.openshell.internal"
MAX_DOCKER_OUTPUT_BYTES = 1024 * 1024
DOCKER_BIN = "/usr/bin/docker"
DOCKER_HOST = "unix:///var/run/docker.sock"
NETWORK_ID_RE = re.compile(r"[0-9a-f]{64}\Z")
RFC1918_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


class BridgeEndpointError(RuntimeError):
    """Stable discovery error that does not echo Docker output."""


@dataclass(frozen=True)
class BridgeEndpoint:
    network_name: str
    network_id: str
    subnet: str
    gateway_ip: str
    host_alias: str = HOST_ALIAS

    def validate(self) -> None:
        if self.network_name != NETWORK_NAME or self.host_alias != HOST_ALIAS:
            raise BridgeEndpointError("bridge_identity_invalid")
        if not NETWORK_ID_RE.fullmatch(self.network_id):
            raise BridgeEndpointError("bridge_network_id_invalid")
        try:
            subnet = ipaddress.ip_network(self.subnet, strict=True)
            gateway = ipaddress.ip_address(self.gateway_ip)
        except ValueError as exc:
            raise BridgeEndpointError("bridge_ipam_invalid") from exc
        if (
            not isinstance(subnet, ipaddress.IPv4Network)
            or not isinstance(gateway, ipaddress.IPv4Address)
            or not any(subnet.subnet_of(private) for private in RFC1918_NETWORKS)
            or gateway not in subnet
            or gateway in {subnet.network_address, subnet.broadcast_address}
            or not gateway.is_private
            or gateway.is_loopback
            or gateway.is_link_local
            or gateway.is_multicast
            or gateway.is_unspecified
            or gateway.is_reserved
        ):
            raise BridgeEndpointError("bridge_ipam_invalid")

    def as_dict(self) -> dict[str, str]:
        self.validate()
        return {
            "schema_version": SCHEMA_VERSION,
            "network_name": self.network_name,
            "network_id": self.network_id,
            "subnet": self.subnet,
            "gateway_ip": self.gateway_ip,
            "host_alias": self.host_alias,
        }


DockerRunner = Callable[[Sequence[str]], subprocess.CompletedProcess[bytes]]


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BridgeEndpointError("docker_inspect_json_invalid")
        result[key] = value
    return result


def _default_docker_runner(command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        binary = Path(DOCKER_BIN)
        info = binary.lstat()
    except OSError as exc:
        raise BridgeEndpointError("docker_binary_untrusted") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != 0
        or stat.S_IMODE(info.st_mode) & 0o022
        or not os.access(binary, os.X_OK)
    ):
        raise BridgeEndpointError("docker_binary_untrusted")
    try:
        return subprocess.run(
            list(command),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BridgeEndpointError("docker_inspect_failed") from exc


def _parse_inspect_payload(content: bytes) -> BridgeEndpoint:
    if not content or len(content) > MAX_DOCKER_OUTPUT_BYTES:
        raise BridgeEndpointError("docker_inspect_json_invalid")
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeEndpointError("docker_inspect_json_invalid") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise BridgeEndpointError("docker_inspect_shape_invalid")
    network = payload[0]
    if (
        network.get("Name") != NETWORK_NAME
        or network.get("Driver") != "bridge"
        or network.get("Scope") != "local"
        or network.get("Internal") is not False
        or network.get("Ingress") is not False
    ):
        raise BridgeEndpointError("docker_network_identity_invalid")
    network_id = network.get("Id")
    if not isinstance(network_id, str) or not NETWORK_ID_RE.fullmatch(network_id):
        raise BridgeEndpointError("bridge_network_id_invalid")
    ipam = network.get("IPAM")
    if not isinstance(ipam, dict) or ipam.get("Driver") not in {"default", ""}:
        raise BridgeEndpointError("docker_ipam_invalid")
    configs = ipam.get("Config")
    if not isinstance(configs, list) or not configs:
        raise BridgeEndpointError("docker_ipam_invalid")
    ipv4_configs: list[tuple[ipaddress.IPv4Network, ipaddress.IPv4Address]] = []
    for config in configs:
        if not isinstance(config, dict):
            raise BridgeEndpointError("docker_ipam_invalid")
        raw_subnet = config.get("Subnet")
        raw_gateway = config.get("Gateway")
        if not isinstance(raw_subnet, str) or not isinstance(raw_gateway, str):
            raise BridgeEndpointError("docker_ipam_invalid")
        try:
            subnet = ipaddress.ip_network(raw_subnet, strict=True)
            gateway = ipaddress.ip_address(raw_gateway)
        except ValueError as exc:
            raise BridgeEndpointError("docker_ipam_invalid") from exc
        if isinstance(subnet, ipaddress.IPv4Network) and isinstance(gateway, ipaddress.IPv4Address):
            ipv4_configs.append((subnet, gateway))
    if len(ipv4_configs) != 1:
        raise BridgeEndpointError("docker_ipv4_gateway_ambiguous")
    subnet, gateway = ipv4_configs[0]
    endpoint = BridgeEndpoint(
        network_name=NETWORK_NAME,
        network_id=network_id,
        subnet=subnet.with_prefixlen,
        gateway_ip=gateway.compressed,
    )
    endpoint.validate()
    return endpoint


def discover_bridge_endpoint(*, runner: DockerRunner = _default_docker_runner) -> BridgeEndpoint:
    command = (DOCKER_BIN, "--host", DOCKER_HOST, "network", "inspect", NETWORK_NAME)
    try:
        completed = runner(command)
    except BridgeEndpointError:
        raise
    except Exception as exc:
        raise BridgeEndpointError("docker_inspect_failed") from exc
    if completed.returncode != 0:
        raise BridgeEndpointError("docker_inspect_failed")
    return _parse_inspect_payload(completed.stdout)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--field",
        choices=("gateway_ip", "host_alias", "network_id", "network_name", "subnet"),
        help="Print one sanitized field instead of the JSON contract",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        endpoint = discover_bridge_endpoint()
    except BridgeEndpointError as exc:
        print(f"bridge discovery failed: {exc}", file=sys.stderr)
        return 2
    if args.field:
        print(endpoint.as_dict()[args.field])
    else:
        print(json.dumps(endpoint.as_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
