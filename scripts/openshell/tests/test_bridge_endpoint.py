from __future__ import annotations

import json
import subprocess
from typing import Sequence

import pytest

from scripts.openshell import bridge_endpoint as module


def _payload(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "Name": module.NETWORK_NAME,
        "Id": "a" * 64,
        "Driver": "bridge",
        "Scope": "local",
        "Internal": False,
        "Ingress": False,
        "IPAM": {
            "Driver": "default",
            "Config": [{"Subnet": "172.28.0.0/16", "Gateway": "172.28.0.1"}],
        },
    }
    value.update(overrides)
    return value


class FakeDocker:
    def __init__(self, payload: object, *, returncode: int = 0) -> None:
        self.payload = payload
        self.returncode = returncode
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(tuple(command))
        return subprocess.CompletedProcess(
            args=list(command),
            returncode=self.returncode,
            stdout=json.dumps(self.payload).encode(),
            stderr=b"never emitted",
        )


def test_discovers_only_fixed_project_bridge_and_returns_sanitized_contract() -> None:
    docker = FakeDocker([_payload()])

    endpoint = module.discover_bridge_endpoint(runner=docker)

    assert docker.calls == [
        (
            "/usr/bin/docker",
            "--host",
            "unix:///var/run/docker.sock",
            "network",
            "inspect",
            "siq-openshell-dev",
        )
    ]
    assert endpoint.as_dict() == {
        "schema_version": module.SCHEMA_VERSION,
        "network_name": "siq-openshell-dev",
        "network_id": "a" * 64,
        "subnet": "172.28.0.0/16",
        "gateway_ip": "172.28.0.1",
        "host_alias": "host.openshell.internal",
    }


@pytest.mark.parametrize(
    "network",
    [
        _payload(Name="bridge"),
        _payload(Driver="overlay"),
        _payload(Scope="swarm"),
        _payload(Internal=True),
        _payload(Ingress=True),
        _payload(Id="short"),
    ],
)
def test_rejects_other_network_identity_or_driver(network: dict[str, object]) -> None:
    with pytest.raises(module.BridgeEndpointError):
        module.discover_bridge_endpoint(runner=FakeDocker([network]))


@pytest.mark.parametrize(
    ("subnet", "gateway"),
    [
        ("0.0.0.0/0", "0.0.0.0"),
        ("8.8.8.0/24", "8.8.8.8"),
        ("127.0.0.0/8", "127.0.0.1"),
        ("169.254.0.0/16", "169.254.1.1"),
        ("172.28.0.0/16", "172.29.0.1"),
        ("172.28.0.0/16", "172.28.0.0"),
        ("172.28.0.0/16", "172.28.255.255"),
        ("100.64.0.0/10", "100.64.0.1"),
    ],
)
def test_rejects_wildcard_public_loopback_linklocal_or_non_rfc1918_gateway(
    subnet: str,
    gateway: str,
) -> None:
    network = _payload(IPAM={"Driver": "default", "Config": [{"Subnet": subnet, "Gateway": gateway}]})
    with pytest.raises(module.BridgeEndpointError):
        module.discover_bridge_endpoint(runner=FakeDocker([network]))


def test_rejects_multiple_ipv4_gateways_as_ambiguous() -> None:
    network = _payload(
        IPAM={
            "Driver": "default",
            "Config": [
                {"Subnet": "172.28.0.0/16", "Gateway": "172.28.0.1"},
                {"Subnet": "10.50.0.0/16", "Gateway": "10.50.0.1"},
            ],
        }
    )
    with pytest.raises(module.BridgeEndpointError, match="ambiguous"):
        module.discover_bridge_endpoint(runner=FakeDocker([network]))


def test_rejects_failed_malformed_duplicate_or_oversized_docker_output() -> None:
    with pytest.raises(module.BridgeEndpointError, match="docker_inspect_failed"):
        module.discover_bridge_endpoint(runner=FakeDocker([], returncode=1))

    def malformed(_command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 0, b"not-json", b"")

    with pytest.raises(module.BridgeEndpointError, match="json_invalid"):
        module.discover_bridge_endpoint(runner=malformed)

    def duplicate(_command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        content = b'[{"Name":"siq-openshell-dev","Name":"other"}]'
        return subprocess.CompletedProcess([], 0, content, b"")

    with pytest.raises(module.BridgeEndpointError, match="json_invalid"):
        module.discover_bridge_endpoint(runner=duplicate)

    def oversized(_command: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess([], 0, b"x" * (module.MAX_DOCKER_OUTPUT_BYTES + 1), b"")

    with pytest.raises(module.BridgeEndpointError, match="json_invalid"):
        module.discover_bridge_endpoint(runner=oversized)


def test_endpoint_dataclass_cannot_be_forged_with_other_alias_or_private_network() -> None:
    with pytest.raises(module.BridgeEndpointError):
        module.BridgeEndpoint(
            network_name=module.NETWORK_NAME,
            network_id="a" * 64,
            subnet="172.28.0.0/16",
            gateway_ip="172.28.0.1",
            host_alias="other.internal",
        ).validate()
    with pytest.raises(module.BridgeEndpointError):
        module.BridgeEndpoint(
            network_name=module.NETWORK_NAME,
            network_id="a" * 64,
            subnet="198.51.100.0/24",
            gateway_ip="198.51.100.1",
        ).validate()
