#!/usr/bin/env python3
"""Verify upload proxy backpressure and bounded spooling in disposable containers."""

from __future__ import annotations

import argparse
import concurrent.futures
import http.client
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SUPPORT_FILE = REPO_ROOT / "scripts" / "maintenance" / "upload_proxy_container_smoke_support.py"
UPLOAD_LIMITS_FILE = REPO_ROOT / "apps" / "api" / "services" / "upload_proxy_limits.py"
DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "local" / "upload-proxy-container-smoke.json"
DEFAULT_IMAGE = "siq-production-smoke-122182-09f980-api:latest"
MIB = 1024 * 1024


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=REPO_ROOT,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_get(url: str, *, timeout: float = 3.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.load(response)


def _wait_health(url: str, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if _json_get(url).get("status") == "ok":
                return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"container did not become healthy: {url}")


def _multipart_body(size_bytes: int, marker: bytes) -> tuple[bytes, str]:
    boundary = f"siq-smoke-{os.urandom(8).hex()}"
    prefix = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="files"; filename="payload.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("ascii")
    suffix = f"\r\n--{boundary}--\r\n".encode("ascii")
    payload = (marker * (size_bytes // len(marker) + 1))[:size_bytes]
    return prefix + payload + suffix, boundary


def _post_upload(port: int, *, size_bytes: int, marker: bytes, timeout: float = 10.0) -> dict[str, Any]:
    body, boundary = _multipart_body(size_bytes, marker)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    started = time.monotonic()
    try:
        connection.request(
            "POST",
            "/upload",
            body=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))},
        )
        response = connection.getresponse()
        raw = response.read()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"raw": raw.decode("utf-8", errors="replace")[:500]}
        return {
            "status_code": response.status,
            "retry_after": response.getheader("Retry-After"),
            "duration_seconds": round(time.monotonic() - started, 3),
            "payload": payload,
        }
    finally:
        connection.close()


def _cgroup_memory_bytes(container: str) -> int:
    result = _run(
        [
            "docker", "exec", container, "python", "-c",
            "from pathlib import Path; print(Path('/sys/fs/cgroup/memory.current').read_text().strip())",
        ]
    )
    return int(result.stdout.strip())


def _container_security(container: str) -> dict[str, Any]:
    inspection = json.loads(_run(["docker", "inspect", container]).stdout)[0]
    host = inspection.get("HostConfig", {})
    return {
        "read_only": host.get("ReadonlyRootfs") is True,
        "capabilities_dropped": "ALL" in (host.get("CapDrop") or []),
        "no_new_privileges": "no-new-privileges:true" in (host.get("SecurityOpt") or []),
        "memory_limit_bytes": int(host.get("Memory") or 0),
        "pids_limit": int(host.get("PidsLimit") or 0),
    }


def assert_smoke_contract(report: dict[str, Any]) -> None:
    if report.get("status") != "passed":
        raise AssertionError(f"smoke status is not passed: {report.get('status')}")
    if report.get("evidence_boundary") != "local_disposable_container":
        raise AssertionError("smoke evidence boundary is not local disposable container")
    if report.get("production_data_touched") is not False or report.get("production_compose_modified") is not False:
        raise AssertionError("smoke must not touch production data or modify production Compose")
    busy = report.get("busy_response") or {}
    if busy.get("status_code") != 503 or busy.get("retry_after") != "1":
        raise AssertionError("concurrency saturation did not return 503 with Retry-After: 1")
    slow = report.get("slow_responses") or []
    if len(slow) != 2 or any(item.get("status_code") != 502 for item in slow):
        raise AssertionError("controlled slow upstream requests did not time out safely")
    recovered = report.get("recovery_response") or {}
    if recovered.get("status_code") != 200:
        raise AssertionError("capacity was not released after the slow upstream timed out")
    metrics = report.get("proxy_metrics") or {}
    if metrics.get("max_active") != metrics.get("limit") or metrics.get("active") != 0:
        raise AssertionError("proxy admission accounting is inconsistent")
    if metrics.get("busy_rejections") != 1 or metrics.get("upstream_timeouts") != 2:
        raise AssertionError("proxy did not record expected saturation and timeout events")
    if metrics.get("rolled_to_disk", 0) < 2:
        raise AssertionError("large uploads did not roll over from memory to disk")
    if metrics.get("closed_files") != metrics.get("buffered_files"):
        raise AssertionError("not all buffered upload handles were closed")
    memory = report.get("container_memory") or {}
    if memory.get("peak_delta_bytes", 0) > memory.get("allowed_peak_delta_bytes", -1):
        raise AssertionError("container memory delta exceeded the explicit smoke budget")
    security = report.get("proxy_container_security") or {}
    if not all(security.get(key) for key in ("read_only", "capabilities_dropped", "no_new_privileges")):
        raise AssertionError("proxy container isolation contract is incomplete")
    if not report.get("cleanup_complete"):
        raise AssertionError("temporary containers or network were not cleaned up")
    if not report.get("not_proven"):
        raise AssertionError("external production residual risks must remain explicit")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--file-mib", type=int, default=8)
    parser.add_argument("--memory-delta-mib", type=int, default=64)
    args = parser.parse_args(argv)
    if not shutil.which("docker"):
        raise RuntimeError("docker is required for this opt-in smoke")
    if _run(["docker", "image", "inspect", args.image], check=False).returncode:
        raise RuntimeError(f"API image is not available: {args.image}; build production Compose first")

    prefix = f"siq-upload-smoke-{os.getpid()}-{os.urandom(3).hex()}"
    network = f"{prefix}-network"
    upstream = f"{prefix}-upstream"
    proxy = f"{prefix}-proxy"
    proxy_port = _available_port()
    containers: list[str] = []
    started = time.monotonic()
    stop_sampling = threading.Event()
    memory_samples: list[int] = []
    report: dict[str, Any] = {
        "schema_version": "siq_upload_proxy_container_smoke_v1",
        "status": "failed",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "evidence_boundary": "local_disposable_container",
        "production_data_touched": False,
        "production_compose_modified": False,
        "file_size_bytes": args.file_mib * MIB,
        "concurrency_limit": 2,
        "not_proven": [
            "external_production_ingress_behavior",
            "external_load_balancer_request_buffering",
            "production_host_memory_pressure",
            "real_parser_network_latency_distribution",
        ],
    }
    try:
        # A disposable private bridge is required here because Docker does not
        # publish ports from an ``internal`` network. The only reachable
        # application peer is the controlled upstream container below.
        _run(["docker", "network", "create", "--subnet", "172.31.240.0/24", network])
        common = [
            "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=96m", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges:true", "--pids-limit", "96",
            "--mount", f"type=bind,src={SUPPORT_FILE},dst=/smoke/support.py,readonly",
        ]
        _run(
            [
                "docker", "run", "--detach", "--name", upstream, "--network", network,
                *common, "--memory", "192m", args.image, "python", "/smoke/support.py", "upstream",
                "--delay-seconds", "2.0",
            ]
        )
        containers.append(upstream)
        _run(
            [
                "docker", "run", "--detach", "--name", proxy, "--network", network,
                "--publish", f"127.0.0.1:{proxy_port}:18081", *common, "--memory", "192m",
                "--mount", f"type=bind,src={UPLOAD_LIMITS_FILE},dst=/app/apps/api/services/upload_proxy_limits.py,readonly",
                "--env", "PYTHONPATH=/app/apps/api",
                "--env", f"SMOKE_UPSTREAM_URL=http://{upstream}:18080/api/tasks",
                "--env", "SMOKE_MAX_CONCURRENCY=2", "--env", "SMOKE_QUEUE_TIMEOUT_SECONDS=0.25",
                "--env", "SMOKE_UPSTREAM_READ_TIMEOUT_SECONDS=0.75", "--env", "SMOKE_SPOOL_MAX_BYTES=1048576",
                args.image, "python", "/smoke/support.py", "proxy",
            ]
        )
        containers.append(proxy)
        base_url = f"http://127.0.0.1:{proxy_port}"
        _wait_health(f"{base_url}/health")
        baseline = _cgroup_memory_bytes(proxy)
        memory_samples.append(baseline)

        def sample_memory() -> None:
            while not stop_sampling.wait(0.05):
                try:
                    memory_samples.append(_cgroup_memory_bytes(proxy))
                except Exception:
                    return

        sampler = threading.Thread(target=sample_memory, daemon=True)
        sampler.start()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(
                    _post_upload,
                    proxy_port,
                    size_bytes=args.file_mib * MIB,
                    marker=marker,
                )
                for marker in (b"A", b"B")
            ]
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                if _json_get(f"{base_url}/metrics").get("active") == 2:
                    break
                time.sleep(0.05)
            else:
                raise RuntimeError("two slow uploads did not occupy both admission slots")
            busy_response = _post_upload(proxy_port, size_bytes=2 * MIB, marker=b"C")
            slow_responses = [future.result(timeout=10) for future in futures]
        recovery_response = _post_upload(proxy_port, size_bytes=256 * 1024, marker=b"D")
        stop_sampling.set()
        sampler.join(timeout=2)
        memory_samples.append(_cgroup_memory_bytes(proxy))
        metrics = _json_get(f"{base_url}/metrics")
        peak = max(memory_samples)
        report.update(
            {
                "busy_response": busy_response,
                "slow_responses": slow_responses,
                "recovery_response": recovery_response,
                "proxy_metrics": metrics,
                "container_memory": {
                    "baseline_bytes": baseline,
                    "peak_bytes": peak,
                    "peak_delta_bytes": max(0, peak - baseline),
                    "allowed_peak_delta_bytes": args.memory_delta_mib * MIB,
                    "sample_count": len(memory_samples),
                    "measurement": "container_cgroup_memory.current",
                },
                "proxy_container_security": _container_security(proxy),
                "upstream_container_security": _container_security(upstream),
                "network_internal": json.loads(_run(["docker", "network", "inspect", network]).stdout)[0].get("Internal") is True,
                "network_mode": "disposable_private_bridge",
                "image": args.image,
                "image_id": _run(["docker", "image", "inspect", args.image, "--format", "{{.Id}}"]).stdout.strip(),
                "status": "passed",
            }
        )
    except Exception as exc:
        report["error"] = str(exc)
        raise
    finally:
        stop_sampling.set()
        for container in reversed(containers):
            _run(["docker", "rm", "--force", container], check=False)
        _run(["docker", "network", "rm", network], check=False)
        report["cleanup_complete"] = all(
            _run(["docker", "container", "inspect", container], check=False).returncode != 0
            for container in containers
        ) and _run(["docker", "network", "inspect", network], check=False).returncode != 0
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
