#!/usr/bin/env python3
"""Exercise report-finder SSRF defenses in disposable Docker networks."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORT_FILE = REPO_ROOT / "scripts" / "maintenance" / "report_finder_egress_smoke_support.py"
COMPOSE_FILE = REPO_ROOT / "infra" / "docker" / "docker-compose.yml"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "local" / "report-finder-egress-smoke.json"
DEFAULT_IMAGE = "siq-report-finder-egress-smoke:local"
NETWORKS = {
    "public": ("93.184.216.0/24", "93.184.216.10"),
    "private": ("10.77.0.0/24", "10.77.0.10"),
    "linklocal": ("169.254.240.0/24", "169.254.240.10"),
    "metadata": ("169.254.169.0/24", "169.254.169.254"),
}


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, cwd=REPO_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _wait_for(path: Path, timeout: float = 15.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.1)
    raise RuntimeError(f"container fixture did not become ready: {path.name}")


def production_compose_network_audit(compose_text: str) -> dict[str, object]:
    marker = "\n  report-finder:\n"
    if marker not in compose_text:
        raise AssertionError("production compose is missing report-finder service")
    service_lines: list[str] = []
    for line in compose_text.split(marker, 1)[1].splitlines():
        if line.startswith("  ") and not line.startswith("    "):
            break
        service_lines.append(line)
    service_section = "\n".join(service_lines)
    return {
        "cap_drop_all": "cap_drop:\n      - ALL" in service_section,
        "explicit_egress_proxy": any(
            variable in service_section for variable in ("HTTP_PROXY=", "HTTPS_PROXY=", "ALL_PROXY=")
        ),
        "explicit_internal_network": "internal: true" in service_section,
        "infrastructure_egress_policy_proven": False,
    }


def assert_smoke_contract(report: dict[str, object]) -> None:
    if report.get("status") != "passed":
        raise AssertionError(f"smoke status is not passed: {report.get('status')}")
    if report.get("production_compose_modified"):
        raise AssertionError("test-only egress smoke modified production compose")
    compose_audit = report.get("production_compose_network_audit")
    if not isinstance(compose_audit, dict) or not compose_audit.get("cap_drop_all"):
        raise AssertionError("production report-finder capability boundary was not audited")
    if compose_audit.get("infrastructure_egress_policy_proven") is not False:
        raise AssertionError("smoke must not claim an external infrastructure egress policy")
    if not report.get("all_networks_internal"):
        raise AssertionError("all test networks must be Docker internal networks")
    if not report.get("runner_read_only") or not report.get("runner_capabilities_dropped"):
        raise AssertionError("runner isolation was not proven")
    if not report.get("runner_no_new_privileges") or not report.get("cleanup_complete"):
        raise AssertionError("runner security or cleanup contract was not proven")
    official = report.get("official_allowlist")
    if not isinstance(official, dict) or official.get("status_code") != 200 or official.get("connect_attempts") != 1:
        raise AssertionError("controlled official allowlist request did not use exactly one real connection")
    if official.get("connected_ip") != "93.184.216.10" or not official.get("body_verified"):
        raise AssertionError("official request was not pinned to the controlled public stub")
    if official.get("host_header") != "www.sec.gov:18080" or not official.get("policy_validated"):
        raise AssertionError("official allowlist policy or original Host header was not proven")
    blocked = report.get("blocked_destinations")
    if not isinstance(blocked, dict) or set(blocked) != {"private", "link_local", "metadata", "loopback"}:
        raise AssertionError("blocked destination matrix is incomplete")
    for name, result in blocked.items():
        if not isinstance(result, dict) or not result.get("blocked_before_connect") or result.get("trap_hits") != 0:
            raise AssertionError(f"{name} was not blocked before connect")
    redirect = report.get("redirect_to_metadata")
    if (
        not isinstance(redirect, dict)
        or not redirect.get("initial_redirect_observed")
        or not redirect.get("blocked_before_second_connect")
        or redirect.get("metadata_trap_hits") != 0
    ):
        raise AssertionError("redirect to metadata was not blocked before its connect")
    rebind = report.get("dns_rebind")
    if not isinstance(rebind, dict) or not rebind.get("blocked_before_connect") or rebind.get("official_stub_hits") != 0:
        raise AssertionError("DNS rebind was not blocked before connect")
    observations = report.get("dns_observations")
    if not isinstance(observations, list):
        raise AssertionError("container DNS observations are missing")
    answers: dict[str, list[object]] = {}
    for item in observations:
        if isinstance(item, dict):
            answers.setdefault(str(item.get("name")), []).append(item.get("address"))
    expected_answers = {
        "private.sec.gov": ["10.77.0.10"],
        "linklocal.sec.gov": ["169.254.240.10"],
        "metadata.sec.gov": ["169.254.169.254"],
        "loopback.sec.gov": ["127.0.0.1"],
        "rebind.sec.gov": ["93.184.216.10", "127.0.0.1"],
    }
    for name, expected in expected_answers.items():
        if answers.get(name) != expected:
            raise AssertionError(f"unexpected DNS answer sequence for {name}: {answers.get(name)}")


def _container_args(*, name: str, network: str, output: Path, image: str) -> list[str]:
    return [
        "docker", "create", "--name", name,
        "--network", network,
        "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges:true",
        "--pids-limit", "64",
        "--mount", f"type=bind,src={SUPPORT_FILE},dst=/smoke/support.py,readonly",
        "--mount", f"type=bind,src={output},dst=/out",
        image,
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--no-build", action="store_true", help="reuse the named image instead of building current source")
    args = parser.parse_args(argv)
    if not shutil.which("docker"):
        raise RuntimeError("docker is required for this opt-in smoke")
    if not args.no_build:
        _run(["docker", "build", "-t", args.image, str(REPO_ROOT / "services" / "market-report-finder")])

    prefix = f"siq-egress-smoke-{os.getpid()}-{os.urandom(3).hex()}"
    compose_before = _sha256(COMPOSE_FILE)
    networks = {key: f"{prefix}-{key}" for key in NETWORKS}
    containers: list[str] = []
    started = time.monotonic()
    report: dict[str, object] = {
        "schema_version": "siq_report_finder_egress_smoke_v1",
        "status": "failed",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "production_compose_sha256_before": compose_before,
        "production_compose_network_audit": production_compose_network_audit(
            COMPOSE_FILE.read_text(encoding="utf-8")
        ),
        "not_proven": [
            "external_cloud_firewall",
            "production_kubernetes_network_policy",
            "production_host_firewall",
            "public_internet_availability",
        ],
    }
    try:
        with tempfile.TemporaryDirectory(prefix="siq-egress-smoke-") as temp:
            shared = Path(temp)
            shared.chmod(0o777)
            for key, (subnet, _address) in NETWORKS.items():
                _run(["docker", "network", "create", "--internal", "--subnet", subnet, networks[key]])

            fixtures = [
                ("official", "public", "93.184.216.10", 18080),
                ("private-trap", "private", "10.77.0.10", 18083),
                ("linklocal-trap", "linklocal", "169.254.240.10", 18084),
                ("metadata-trap", "metadata", "169.254.169.254", 18081),
            ]
            for name, network_key, address, port in fixtures:
                container = f"{prefix}-{name}"
                command = _container_args(name=container, network=networks[network_key], output=shared, image=args.image)
                command[command.index("--network") + 2 : command.index("--network") + 2] = ["--ip", address]
                command += ["python", "/smoke/support.py", "http", "--name", name, "--port", str(port), "--output", "/out"]
                _run(command)
                containers.append(container)
                _run(["docker", "start", container])

            dns_name = f"{prefix}-dns"
            command = _container_args(name=dns_name, network=networks["public"], output=shared, image=args.image)
            command[command.index("--network") + 2 : command.index("--network") + 2] = ["--ip", "93.184.216.53"]
            command += ["python", "/smoke/support.py", "dns", "--output", "/out"]
            _run(command)
            containers.append(dns_name)
            _run(["docker", "start", dns_name])
            for ready in ("official", "private-trap", "linklocal-trap", "metadata-trap", "dns"):
                _wait_for(shared / f"{ready}.ready")

            runner = f"{prefix}-runner"
            command = _container_args(name=runner, network=networks["public"], output=shared, image=args.image)
            command[command.index("--network") + 2 : command.index("--network") + 2] = [
                "--dns", "93.184.216.53", "--add-host", "www.sec.gov:93.184.216.10",
            ]
            command += ["python", "/smoke/support.py", "driver", "--output", "/out"]
            _run(command)
            containers.append(runner)
            for key in ("private", "linklocal", "metadata"):
                _run(["docker", "network", "connect", networks[key], runner])
            runner_result = _run(["docker", "start", "--attach", runner], check=False)
            if runner_result.returncode:
                raise RuntimeError(f"container driver failed:\n{runner_result.stdout}")
            driver_report = json.loads((shared / "driver-report.json").read_text(encoding="utf-8"))
            report.update(driver_report)
            runner_inspection = json.loads(_run(["docker", "inspect", runner]).stdout)[0]
            host_config = runner_inspection.get("HostConfig", {})
            security_options = host_config.get("SecurityOpt") or []
            image_id = _run(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"]).stdout.strip()
            network_inspection = [
                json.loads(_run(["docker", "network", "inspect", name]).stdout)[0] for name in networks.values()
            ]
            report.update(
                {
                    "all_networks_internal": all(item.get("Internal") is True for item in network_inspection),
                    "image": args.image,
                    "image_id": image_id,
                    "production_compose_modified": _sha256(COMPOSE_FILE) != compose_before,
                    "runner_capabilities_dropped": "ALL" in (host_config.get("CapDrop") or []),
                    "runner_no_new_privileges": "no-new-privileges:true" in security_options,
                    "runner_read_only": host_config.get("ReadonlyRootfs") is True,
                    "status": "passed",
                }
            )
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        for container in reversed(containers):
            _run(["docker", "rm", "--force", container], check=False)
        for network in reversed(list(networks.values())):
            _run(["docker", "network", "rm", network], check=False)
        networks_removed = all(
            _run(["docker", "network", "inspect", name], check=False).returncode != 0 for name in networks.values()
        )
        containers_removed = all(
            _run(["docker", "container", "inspect", name], check=False).returncode != 0 for name in containers
        )
        report["cleanup_complete"] = networks_removed and containers_removed
        report["temporary_containers_removed"] = containers_removed
        report["temporary_networks_removed"] = networks_removed
        if not report["cleanup_complete"]:
            report["status"] = "failed"
        report["duration_seconds"] = round(time.monotonic() - started, 3)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        assert_smoke_contract(report)
    except Exception as exc:
        report["status"] = "failed"
        report["error"] = str(exc)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
