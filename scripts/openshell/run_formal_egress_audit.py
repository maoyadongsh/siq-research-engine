#!/usr/bin/env python3
"""Attach to one running formal sandbox and publish egress plus audit evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import stat
import sys
import threading
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator, FormatChecker

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import (  # noqa: E402
    aggregate_security_audit,
    bridge_endpoint,
    check_sanitized_artifacts,
    probe_siq_analysis_sandbox as sandbox_probe,
    run_egress_boundary_proof as host_egress_proof,
    run_formal_filesystem_boundary as formal_binding,
    security_audit,
)
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    HERMES_COMMIT,
    PROFILE,
    LifecycleError,
)

EGRESS_SCHEMA_VERSION = "siq.openshell.formal-egress-sandbox-evidence.v1"
AUDIT_SCHEMA_VERSION = "siq.openshell.formal-structured-audit-evidence.v1"
RAW_SCHEMA_VERSION = "siq.openshell.formal-egress-audit-raw-receipt.v1"
REPO_ROOT = Path(__file__).resolve().parents[2]
EGRESS_SCHEMA_RELATIVE = Path("infra/openshell/schemas/formal-egress-sandbox-evidence.schema.json")
AUDIT_SCHEMA_RELATIVE = Path("infra/openshell/schemas/formal-structured-audit-evidence.schema.json")
HOST_COMPONENT_RELATIVE = Path("artifacts/openshell/v0.6/egress-boundary.sanitized.json")
EGRESS_JSON_RELATIVE = Path("artifacts/openshell/v0.6/formal-egress-sandbox.sanitized.json")
EGRESS_MARKDOWN_RELATIVE = Path("artifacts/openshell/v0.6/formal-egress-sandbox.sanitized.md")
AUDIT_JSON_RELATIVE = Path("artifacts/openshell/v0.6/formal-structured-audit.sanitized.json")
AUDIT_MARKDOWN_RELATIVE = Path("artifacts/openshell/v0.6/formal-structured-audit.sanitized.md")
RAW_ROOT_RELATIVE = Path("var/openshell/proofs/formal-egress-audit")
RUNNER_RELATIVE = Path("scripts/openshell/run_formal_egress_audit.py")
EGRESS_GUARD_RELATIVE = Path("scripts/openshell/egress_guard.py")
REQUEST_IDENTITY_RELATIVE = Path("scripts/openshell/broker_request_identity.py")
AUDIT_CONTRACT_RELATIVE = Path("scripts/openshell/security_audit.py")
AUDIT_AGGREGATOR_RELATIVE = Path("scripts/openshell/aggregate_security_audit.py")
MAX_AUDIT_FILES = 256
MAX_AUDIT_FILE_BYTES = 64 * 1024 * 1024
TRANSFER_CLIENTS = ["curl_upload", "rclone", "rsync", "scp", "sftp"]
RESOLVER_AUDIT_SCOPE = "egress.mihomo_fake_ip_compat_resolved"


class FormalEgressAuditError(RuntimeError):
    """Stable, content-free failure for the formal egress evidence runner."""

    def __init__(self, code: str) -> None:
        rendered = code if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) else "formal_egress_audit_failed"
        self.code = rendered
        super().__init__(rendered)


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    decision: str
    enforcement_layer: str
    reason_code: str
    executor: str
    outer_http_status: int | None = None
    upstream_required: bool = False


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    decision: str
    enforcement_layer: str
    reason_code: str
    duration_ms: int
    observation_contract: str


@dataclass(frozen=True)
class AuditFileSnapshot:
    device: int
    inode: int
    content: bytes


class ControlledDenyReceiver:
    """Observe whether a denied sandbox binary reaches a real host socket."""

    def __init__(self, address: str) -> None:
        self.address = address
        self.port = 0
        self._stop = threading.Event()
        self._observed = threading.Event()
        self._sockets: list[socket.socket] = []
        self._threads: list[threading.Thread] = []

    def __enter__(self) -> ControlledDenyReceiver:
        tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            tcp.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            tcp.bind((self.address, 0))
            self.port = int(tcp.getsockname()[1])
            udp.bind((self.address, self.port))
            tcp.listen(8)
            tcp.settimeout(0.1)
            udp.settimeout(0.1)
        except Exception:
            tcp.close()
            udp.close()
            raise
        self._sockets = [tcp, udp]
        self._threads = [
            threading.Thread(target=self._observe_tcp, daemon=True),
            threading.Thread(target=self._observe_udp, daemon=True),
        ]
        for thread in self._threads:
            thread.start()
        return self

    def _observe_tcp(self) -> None:
        listener = self._sockets[0]
        while not self._stop.is_set():
            try:
                connection, _address = listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self._observed.set()
            connection.close()

    def _observe_udp(self) -> None:
        listener = self._sockets[1]
        while not self._stop.is_set():
            try:
                listener.recvfrom(4096)
            except TimeoutError:
                continue
            except OSError:
                return
            self._observed.set()

    def assert_unreached(self) -> None:
        if self._observed.wait(0.25):
            raise FormalEgressAuditError("formal_direct_receiver_reached")

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._stop.set()
        for listener in self._sockets:
            listener.close()
        for thread in self._threads:
            thread.join(timeout=1)


def case_specs() -> tuple[CaseSpec, ...]:
    return (
        CaseSpec("public_get", "allow", "egress_broker", "unknown_safe_read", "broker", 200, True),
        CaseSpec("public_head", "allow", "egress_broker", "unknown_safe_read", "broker", 200, True),
        CaseSpec(
            "unknown_small_json",
            "audit_only",
            "egress_broker",
            "unknown_json_post_audit",
            "broker",
            200,
            True,
        ),
        CaseSpec("unknown_multipart", "deny", "egress_guard", "broker_multipart_denied", "broker", 403),
        CaseSpec(
            "unknown_octet_stream",
            "deny",
            "egress_guard",
            "broker_octet_stream_denied",
            "broker",
            403,
        ),
        CaseSpec("unknown_put", "deny", "egress_guard", "broker_method_denied", "broker", 403),
        CaseSpec("oversized_unknown_body", "deny", "egress_guard", "json_body_too_large", "broker", 413),
        CaseSpec(
            "presigned_object_upload",
            "deny",
            "egress_guard",
            "approved_destination_rule_mismatch",
            "broker",
            403,
        ),
        CaseSpec("curl_upload", "deny", "sandbox_network_policy", "direct_egress_denied", "direct"),
        CaseSpec("scp", "deny", "sandbox_network_policy", "direct_egress_denied", "direct"),
        CaseSpec("sftp", "deny", "sandbox_network_policy", "direct_egress_denied", "direct"),
        CaseSpec("rsync", "deny", "sandbox_network_policy", "direct_egress_denied", "direct"),
        CaseSpec("rclone", "deny", "sandbox_network_policy", "direct_egress_denied", "direct"),
        CaseSpec("cloud_metadata", "deny", "egress_guard", "ssrf_non_public_ip", "broker", 403),
        CaseSpec("direct_public_tcp", "deny", "sandbox_network_policy", "direct_egress_denied", "direct"),
        CaseSpec("direct_public_udp", "deny", "sandbox_network_policy", "direct_egress_denied", "direct"),
        CaseSpec(
            "direct_public_websocket",
            "deny",
            "sandbox_network_policy",
            "direct_egress_denied",
            "direct",
        ),
    )


BROKER_CASE_PROBE = r"""
import json
import os
import urllib.error
import urllib.request

case_id = __import__("sys").argv[1]
token = os.environ.get("SIQ_OPENSHELL_EGRESS_IDENTITY_TOKEN", "")
if not token or len(token) > 4096:
    raise SystemExit(2)

public = "https://example.com/"
upload = "https://example.com/siq-formal-egress-probe"
envelopes = {
    "public_get": {"method": "GET", "url": public},
    "public_head": {"method": "HEAD", "url": public},
    "unknown_small_json": {"method": "POST", "url": public, "json_body": {"probe": "fixed"}},
    "unknown_multipart": {
        "method": "POST", "url": upload, "json_body": {"probe": "fixed"},
        "headers": {"Content-Type": "multipart/form-data"},
    },
    "unknown_octet_stream": {
        "method": "POST", "url": upload, "json_body": {"probe": "fixed"},
        "headers": {"Content-Type": "application/octet-stream"},
    },
    "unknown_put": {"method": "PUT", "url": upload, "json_body": {"probe": "fixed"}},
    "oversized_unknown_body": {
        "method": "POST", "url": public, "json_body": {"probe": "x" * 131072},
    },
    "presigned_object_upload": {
        "method": "POST",
        "url": "https://uploads.github.com/repos/siq/formal/releases/1/assets?name=fixed",
        "json_body": {"probe": "fixed"},
    },
    "cloud_metadata": {"method": "GET", "url": "http://169.254.169.254/latest/meta-data/"},
}
expected = {
    "public_get": (200, "allow", "unknown_safe_read", True),
    "public_head": (200, "allow", "unknown_safe_read", True),
    "unknown_small_json": (200, "audit_only", "unknown_json_post_audit", True),
    "unknown_multipart": (403, "deny", "broker_multipart_denied", False),
    "unknown_octet_stream": (403, "deny", "broker_octet_stream_denied", False),
    "unknown_put": (403, "deny", "broker_method_denied", False),
    "oversized_unknown_body": (413, "deny", "json_body_too_large", False),
    "presigned_object_upload": (403, "deny", "approved_destination_rule_mismatch", False),
    "cloud_metadata": (403, "deny", "ssrf_non_public_ip", False),
}
if case_id not in envelopes:
    raise SystemExit(2)
body = json.dumps(envelopes[case_id], ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
request = urllib.request.Request(
    "http://host.openshell.internal:18792/v1/request",
    data=body,
    headers={"Content-Type": "application/json", "X-SIQ-OpenShell-Identity": token},
    method="POST",
)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request, timeout=20) as response:
        status = response.status
        content = response.read(1048577)
except urllib.error.HTTPError as exc:
    status = exc.code
    content = exc.read(1048577)
if len(content) > 1048576:
    raise SystemExit(2)
try:
    result = json.loads(content)
except (UnicodeDecodeError, json.JSONDecodeError):
    raise SystemExit(2)
if not isinstance(result, dict):
    raise SystemExit(2)
expected_status, expected_decision, expected_reason, upstream_required = expected[case_id]
egress = result.get("egress")
if expected_decision in {"allow", "audit_only"}:
    upstream = result.get("status")
    if (
        status != expected_status
        or result.get("ok") is not True
        or not isinstance(egress, dict)
        or egress.get("decision") != expected_decision
        or egress.get("rule_id") != expected_reason
        or isinstance(upstream, bool)
        or not isinstance(upstream, int)
        or not 100 <= upstream <= 599
    ):
        raise SystemExit(3)
else:
    if status != expected_status or result.get("ok") is not False or result.get("error_code") != expected_reason:
        raise SystemExit(3)
    if case_id != "oversized_unknown_body" and (
        not isinstance(egress, dict)
        or egress.get("decision") != expected_decision
        or egress.get("rule_id") != expected_reason
    ):
        raise SystemExit(3)
print(json.dumps({
    "case_id": case_id,
    "decision": expected_decision,
    "ok": True,
    "outer_http_status": status,
    "reason_code": expected_reason,
    "upstream_required": upstream_required,
}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
"""


DIRECT_CASE_PROBE = r"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile

case_id = sys.argv[1]
receiver_host = sys.argv[2]
try:
    receiver_port = int(sys.argv[3])
except (IndexError, ValueError):
    raise SystemExit(2)
if receiver_host != "host.openshell.internal" or not 1024 <= receiver_port <= 65535:
    raise SystemExit(2)
paths = {
    "curl_upload": "/usr/bin/curl",
    "rclone": "/usr/bin/rclone",
    "rsync": "/usr/bin/rsync",
    "scp": "/usr/bin/scp",
    "sftp": "/usr/bin/sftp",
}
temporary = tempfile.mkdtemp(prefix=".siq-formal-egress-")
probe_file = os.path.join(temporary, "fixed-empty")
with open(probe_file, "xb"):
    pass
clean_env = {"HOME": temporary, "LANG": "C", "LC_ALL": "C", "PATH": "/usr/bin:/bin"}

def run_client(argv):
    try:
        result = subprocess.run(
            argv,
            cwd=temporary,
            env=clean_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise SystemExit(3)
    if result.returncode == 0:
        raise SystemExit(3)
    if len(result.stdout) > 4096 or len(result.stderr) > 65536:
        raise SystemExit(3)
    return result.stdout, result.stderr, False

def require_network_refusal(stderr, timed_out):
    if timed_out:
        return
    text = stderr.decode("utf-8", "replace").lower()
    markers = (
        "network is unreachable",
        "no route to host",
        "operation not permitted",
        "connection timed out",
        "i/o timeout",
        "context deadline exceeded",
        "failed to connect",
        "couldn't connect",
        "could not connect",
    )
    if not any(marker in text for marker in markers):
        raise SystemExit(3)

try:
    if case_id in paths:
        executable = paths[case_id]
        if not os.path.isfile(executable) or not os.access(executable, os.X_OK):
            raise SystemExit(2)
        if case_id == "curl_upload":
            output, error, timed_out = run_client([
                executable, "--noproxy", "*", "--proto", "=http",
                "--connect-timeout", "3", "--max-time", "5", "--upload-file", probe_file,
                "--output", "/dev/null", "--write-out", "%{http_code}",
                "http://%s:%d/siq-formal-egress-probe" % (receiver_host, receiver_port),
            ])
            if not timed_out and output != b"000":
                raise SystemExit(3)
            require_network_refusal(error, timed_out)
        elif case_id == "scp":
            _output, error, timed_out = run_client([
                executable, "-F", "/dev/null", "-B", "-P", str(receiver_port), "-oConnectTimeout=3",
                "-oStrictHostKeyChecking=no", "-oUserKnownHostsFile=/dev/null", probe_file,
                "probe@%s:/formal-probe" % receiver_host,
            ])
            require_network_refusal(error, timed_out)
        elif case_id == "sftp":
            _output, error, timed_out = run_client([
                executable, "-F", "/dev/null", "-P", str(receiver_port), "-oBatchMode=yes",
                "-oConnectTimeout=3", "-oStrictHostKeyChecking=no", "-oUserKnownHostsFile=/dev/null",
                "probe@%s:/formal-probe" % receiver_host,
            ])
            require_network_refusal(error, timed_out)
        elif case_id == "rsync":
            _output, error, timed_out = run_client([
                executable, "--no-motd", "--timeout=3", probe_file,
                "rsync://%s:%d/formal-probe" % (receiver_host, receiver_port),
            ])
            require_network_refusal(error, timed_out)
        elif case_id == "rclone":
            _output, error, timed_out = run_client([
                executable, "copyto", probe_file,
                ":sftp,host=%s,port=%d,user=probe:/formal-probe" % (receiver_host, receiver_port),
                "--contimeout", "3s", "--timeout", "5s", "--retries", "1",
                "--low-level-retries", "1",
            ])
            require_network_refusal(error, timed_out)
    elif case_id in {"direct_public_tcp", "direct_public_websocket"}:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3)
        try:
            sock.connect((receiver_host, receiver_port))
        except OSError:
            pass
        else:
            raise SystemExit(3)
        finally:
            sock.close()
    elif case_id == "direct_public_udp":
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        try:
            sock.connect((receiver_host, receiver_port))
            sock.send(b"\x00\x00\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00")
        except OSError:
            pass
        else:
            raise SystemExit(3)
        finally:
            sock.close()
    else:
        raise SystemExit(2)
finally:
    shutil.rmtree(temporary, ignore_errors=True)
print(json.dumps({
    "case_id": case_id,
    "decision": "deny",
    "failure_class": "network_refusal",
    "ok": True,
    "reason_code": "direct_egress_denied",
}, ensure_ascii=True, separators=(",", ":"), sort_keys=True))
"""


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_sha256(value: Any) -> str:
    return _sha256(json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _source_bytes(root: Path, relative: Path) -> bytes:
    try:
        return formal_binding._stable_file(root / relative)
    except formal_binding.FormalFilesystemEvidenceError as exc:
        raise FormalEgressAuditError("formal_source_binding_invalid") from exc


def _source_sha256(root: Path, relative: Path) -> str:
    return _sha256(_source_bytes(root, relative))


def _validate_schema(payload: Mapping[str, Any], schema_bytes: bytes, *, code: str) -> None:
    try:
        schema = json.loads(schema_bytes)
        Draft202012Validator.check_schema(schema)
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(dict(payload))
    except Exception as exc:
        raise FormalEgressAuditError(code) from exc


def _read_host_component(root: Path, path: Path, *, now: int) -> tuple[dict[str, Any], str, str]:
    candidate = path if path.is_absolute() else root / path
    try:
        resolved = candidate.resolve(strict=True)
        relative = resolved.relative_to(root).as_posix()
        formal_binding._assert_no_symlink_chain(root, resolved)
    except (LifecycleError, OSError, ValueError) as exc:
        raise FormalEgressAuditError("host_egress_component_invalid") from exc
    if not relative.startswith("artifacts/openshell/v0.6/") or not relative.endswith(".sanitized.json"):
        raise FormalEgressAuditError("host_egress_component_invalid")
    content = _source_bytes(root, Path(relative))
    try:
        payload = json.loads(content)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise FormalEgressAuditError("host_egress_component_invalid") from exc
    if not isinstance(payload, dict):
        raise FormalEgressAuditError("host_egress_component_invalid")
    try:
        host_egress_proof._validate_evidence(payload)
        schema = json.loads(_source_bytes(root, host_egress_proof.SCHEMA_RELATIVE))
        Draft202012Validator(schema).validate(payload)
        current_sources = host_egress_proof._source_digests(root)
    except Exception as exc:
        raise FormalEgressAuditError("host_egress_component_invalid") from exc
    environment = payload.get("environment_binding")
    captured = payload.get("captured_at_unix")
    valid_until = payload.get("valid_until_unix")
    if (
        not isinstance(environment, dict)
        or any(environment.get(key) != value for key, value in current_sources.items())
        or isinstance(captured, bool)
        or not isinstance(captured, int)
        or isinstance(valid_until, bool)
        or not isinstance(valid_until, int)
        or not captured <= now <= valid_until
    ):
        raise FormalEgressAuditError("host_egress_component_invalid")
    try:
        host_egress_proof._assert_strict_brokers(
            root,
            expected_allowlist_contract_sha256=current_sources["allowlist_contract_sha256"],
            expected_source_bundle_sha256=current_sources["runtime_source_bundle_sha256"],
        )
    except host_egress_proof.EgressBoundaryProofError as exc:
        raise FormalEgressAuditError("strict_egress_broker_unavailable") from exc
    return payload, _sha256(content), relative


def _validate_network_contract(capture: formal_binding.ActiveCapture, *, timeout: int) -> str:
    context = capture.context
    try:
        sandbox_probe._validate_active_policy(context, timeout=timeout)
        mounts = sandbox_probe._docker_inspect_mounts(context, timeout=timeout)
        counts = sandbox_probe.validate_container_mounts(context, mounts)
    except sandbox_probe.ProbeError as exc:
        raise FormalEgressAuditError("formal_network_binding_invalid") from exc
    if counts != {"business_mount_count": 7, "control_mount_count": 5, "total_mount_count": 12}:
        raise FormalEgressAuditError("formal_network_binding_invalid")
    network = context.policy.get("network_policies")
    if not isinstance(network, dict) or set(network) != {
        "siq_data_broker",
        "siq_egress_guard",
        "siq_internal_services",
    }:
        raise FormalEgressAuditError("formal_network_policy_invalid")
    expected_ports = {
        "siq_data_broker": {18793},
        "siq_egress_guard": {18792},
        "siq_internal_services": {8004, 8006, 8007, 8013},
    }
    all_binaries: dict[str, set[str]] = {}
    allowed_gateway_ips: set[str] = set()
    for name, expected in expected_ports.items():
        rule = network.get(name)
        endpoints = rule.get("endpoints") if isinstance(rule, dict) else None
        binaries = rule.get("binaries") if isinstance(rule, dict) else None
        if not isinstance(endpoints, list) or not isinstance(binaries, list):
            raise FormalEgressAuditError("formal_network_policy_invalid")
        observed_ports: set[int] = set()
        for endpoint in endpoints:
            if (
                not isinstance(endpoint, dict)
                or endpoint.get("host") != "host.openshell.internal"
                or not isinstance(endpoint.get("allowed_ips"), list)
                or len(endpoint["allowed_ips"]) != 1
                or any(not isinstance(value, str) or not value.endswith("/32") for value in endpoint["allowed_ips"])
            ):
                raise FormalEgressAuditError("formal_network_policy_invalid")
            allowed_gateway_ips.update(endpoint["allowed_ips"])
            port = endpoint.get("port")
            if isinstance(port, bool) or not isinstance(port, int):
                raise FormalEgressAuditError("formal_network_policy_invalid")
            observed_ports.add(port)
        if observed_ports != expected:
            raise FormalEgressAuditError("formal_network_policy_invalid")
        paths = {
            str(item.get("path"))
            for item in binaries
            if isinstance(item, dict) and isinstance(item.get("path"), str)
        }
        if len(paths) != len(binaries):
            raise FormalEgressAuditError("formal_network_policy_invalid")
        all_binaries[name] = paths
    if "/usr/bin/curl" not in all_binaries["siq_egress_guard"]:
        raise FormalEgressAuditError("formal_network_policy_invalid")
    forbidden_clients = {"/usr/bin/rclone", "/usr/bin/rsync", "/usr/bin/scp", "/usr/bin/sftp"}
    if any(forbidden_clients & paths for paths in all_binaries.values()):
        raise FormalEgressAuditError("formal_network_policy_invalid")
    try:
        bridge = bridge_endpoint.discover_bridge_endpoint()
    except bridge_endpoint.BridgeEndpointError as exc:
        raise FormalEgressAuditError("formal_network_bridge_invalid") from exc
    if allowed_gateway_ips != {f"{bridge.gateway_ip}/32"}:
        raise FormalEgressAuditError("formal_network_policy_invalid")
    return bridge.gateway_ip


def _audit_directory(root: Path) -> Path:
    directory = root / security_audit.AUDIT_RELATIVE_ROOT
    try:
        info = directory.lstat()
    except OSError as exc:
        raise FormalEgressAuditError("formal_audit_directory_invalid") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise FormalEgressAuditError("formal_audit_directory_invalid")
    return directory


def _read_audit_file(path: Path) -> tuple[bytes, os.stat_result]:
    descriptor = -1
    try:
        initial = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            stat.S_ISLNK(initial.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != 1
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_size > MAX_AUDIT_FILE_BYTES
            or (opened.st_dev, opened.st_ino) != (initial.st_dev, initial.st_ino)
        ):
            raise FormalEgressAuditError("formal_audit_file_invalid")
        content = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, MAX_AUDIT_FILE_BYTES + 1 - len(content))):
            content.extend(chunk)
            if len(content) > MAX_AUDIT_FILE_BYTES:
                raise FormalEgressAuditError("formal_audit_file_invalid")
        finished = os.fstat(descriptor)
        final = path.lstat()
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            finished.st_dev,
            finished.st_ino,
            finished.st_size,
        ) or (opened.st_dev, opened.st_ino, opened.st_size) != (
            final.st_dev,
            final.st_ino,
            final.st_size,
        ):
            raise FormalEgressAuditError("formal_audit_file_changed")
        return bytes(content), finished
    except FormalEgressAuditError:
        raise
    except OSError as exc:
        raise FormalEgressAuditError("formal_audit_file_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _audit_snapshot(root: Path) -> dict[str, AuditFileSnapshot]:
    directory = _audit_directory(root)
    candidates = sorted(directory.glob("*.jsonl"), key=lambda item: item.name)
    if len(candidates) > MAX_AUDIT_FILES:
        raise FormalEgressAuditError("formal_audit_file_count_invalid")
    result: dict[str, AuditFileSnapshot] = {}
    for path in candidates:
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\.jsonl", path.name) is None:
            raise FormalEgressAuditError("formal_audit_file_invalid")
        content, info = _read_audit_file(path)
        result[path.name] = AuditFileSnapshot(info.st_dev, info.st_ino, content)
    return result


def _audit_delta(
    root: Path,
    before: Mapping[str, AuditFileSnapshot],
) -> list[dict[str, Any]]:
    after = _audit_snapshot(root)
    if not set(before).issubset(after):
        raise FormalEgressAuditError("formal_audit_history_changed")
    records: list[dict[str, Any]] = []
    for name in sorted(after):
        prior = before.get(name)
        current = after[name]
        if prior is not None and (prior.device, prior.inode) != (current.device, current.inode):
            raise FormalEgressAuditError("formal_audit_history_changed")
        prefix = prior.content if prior is not None else b""
        if not current.content.startswith(prefix):
            raise FormalEgressAuditError("formal_audit_history_changed")
        delta = current.content[len(prefix) :]
        if not delta:
            continue
        if not delta.endswith(b"\n"):
            raise FormalEgressAuditError("formal_audit_delta_invalid")
        for line in delta.splitlines():
            if not line or len(line) >= security_audit.MAX_RECORD_BYTES:
                raise FormalEgressAuditError("formal_audit_delta_invalid")
            try:
                payload = json.loads(line)
                record = aggregate_security_audit.validate_record(payload)
            except Exception as exc:
                raise FormalEgressAuditError("formal_audit_delta_invalid") from exc
            if security_audit.serialize_record(record).rstrip(b"\n") != line:
                raise FormalEgressAuditError("formal_audit_delta_noncanonical")
            records.append(record)
    return records


def _audit_context(capture: formal_binding.ActiveCapture) -> security_audit.SecurityRunContext:
    return security_audit.SecurityRunContext(
        profile=PROFILE,
        sandbox_id=capture.context.sandbox_name,
        run_id=capture.run_id,
        session_id=capture.run_id,
        policy_digest=capture.binding.policy_sha256,
    )


def _append_observation(
    root: Path,
    capture: formal_binding.ActiveCapture,
    *,
    operation_class: str,
    scope: str,
    decision: str,
    error_code: str,
    duration_ms: int,
) -> None:
    try:
        record = security_audit.build_record(
            context=_audit_context(capture),
            operation_class=operation_class,
            target=security_audit.project_target(kind="service", scope=scope, value=scope),
            decision=decision,
            error_code=error_code,
            duration_ms=duration_ms,
        )
        security_audit.append_record(project_root=root, record=record)
    except (OSError, security_audit.SecurityAuditError) as exc:
        raise FormalEgressAuditError("formal_audit_append_failed") from exc


def _run_runtime_health(capture: formal_binding.ActiveCapture, *, timeout: int) -> int:
    started = time.monotonic()
    try:
        result = sandbox_probe._sandbox_exec(
            capture.context,
            [sandbox_probe.SANDBOX_PYTHON, "/opt/siq/healthcheck.py"],
            timeout=timeout,
            code="formal_runtime_health_probe_failed",
        )
    except sandbox_probe.ProbeError as exc:
        raise FormalEgressAuditError("formal_runtime_health_probe_failed") from exc
    if result.stdout or result.stderr:
        raise FormalEgressAuditError("formal_runtime_health_probe_invalid")
    return max(0, min(int((time.monotonic() - started) * 1000), 86_400_000))


def _run_case(
    capture: formal_binding.ActiveCapture,
    spec: CaseSpec,
    *,
    timeout: int,
    gateway_ip: str,
) -> CaseResult:
    command = BROKER_CASE_PROBE if spec.executor == "broker" else DIRECT_CASE_PROBE
    started = time.monotonic()
    try:
        if spec.executor == "direct":
            with ControlledDenyReceiver(gateway_ip) as receiver:
                value = sandbox_probe._sandbox_exec_json(
                    capture.context,
                    [
                        sandbox_probe.SANDBOX_PYTHON,
                        "-c",
                        command,
                        spec.case_id,
                        bridge_endpoint.HOST_ALIAS,
                        str(receiver.port),
                    ],
                    timeout=timeout,
                    code=f"formal_egress_{spec.case_id}_failed",
                )
                receiver.assert_unreached()
        else:
            value = sandbox_probe._sandbox_exec_json(
                capture.context,
                [sandbox_probe.SANDBOX_PYTHON, "-c", command, spec.case_id],
                timeout=timeout,
                code=f"formal_egress_{spec.case_id}_failed",
            )
    except sandbox_probe.ProbeError as exc:
        raise FormalEgressAuditError(f"formal_egress_{spec.case_id}_failed") from exc
    except OSError as exc:
        raise FormalEgressAuditError("formal_direct_receiver_invalid") from exc
    duration_ms = max(0, min(int((time.monotonic() - started) * 1000), 86_400_000))
    expected_fields = {"case_id", "decision", "ok", "reason_code"}
    if spec.executor == "broker":
        expected_fields |= {"outer_http_status", "upstream_required"}
    else:
        expected_fields.add("failure_class")
    if (
        set(value) != expected_fields
        or value.get("ok") is not True
        or value.get("case_id") != spec.case_id
        or value.get("decision") != spec.decision
        or value.get("reason_code") != spec.reason_code
        or (
            spec.executor == "broker"
            and (
                value.get("outer_http_status") != spec.outer_http_status
                or value.get("upstream_required") is not spec.upstream_required
            )
        )
        or (spec.executor == "direct" and value.get("failure_class") != "network_refusal")
    ):
        raise FormalEgressAuditError("formal_egress_case_result_invalid")
    if spec.executor == "direct":
        _append_observation(
            capture.context.project_root,
            capture,
            operation_class="network.request",
            scope=f"formal_egress.{spec.case_id}",
            decision="deny",
            error_code=spec.reason_code,
            duration_ms=duration_ms,
        )
    return CaseResult(
        case_id=spec.case_id,
        decision=spec.decision,
        enforcement_layer=spec.enforcement_layer,
        reason_code=spec.reason_code,
        duration_ms=duration_ms,
        observation_contract=(
            "controlled_receiver_no_connection" if spec.executor == "direct" else "broker_decision_receipt"
        ),
    )


def _record_digest(record: Mapping[str, Any]) -> str:
    return _sha256(security_audit.serialize_record(record))


def _select_bound_records(
    records: Sequence[Mapping[str, Any]],
    capture: formal_binding.ActiveCapture,
    results: Sequence[CaseResult],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    expected_session = hashlib.sha256(f"session\0{capture.run_id}".encode()).hexdigest()[:24]
    relevant: list[dict[str, Any]] = []
    for raw in records:
        record = dict(raw)
        if record.get("siq_run_id") != capture.run_id:
            continue
        if (
            record.get("profile") != PROFILE
            or record.get("sandbox_id") != capture.context.sandbox_name
            or record.get("session_projection") != expected_session
            or record.get("policy_digest") != capture.binding.policy_sha256
        ):
            raise FormalEgressAuditError("formal_audit_identity_mismatch")
        relevant.append(record)
    if not relevant:
        raise FormalEgressAuditError("formal_audit_records_missing")

    expected: list[tuple[str, str, str, str, str]] = [
        ("observation:before", "sandbox.lifecycle", "formal_transaction.before", "allow", ""),
        ("observation:health", "service.preflight", "formal_runtime.health", "allow", ""),
    ]
    for result in results:
        scope = (
            f"egress.{result.reason_code}"
            if result.enforcement_layer != "sandbox_network_policy"
            else f"formal_egress.{result.case_id}"
        )
        error_code = result.reason_code if result.decision == "deny" else ""
        expected.append((result.case_id, "network.request", scope, result.decision, error_code))
    expected.append(("observation:after", "sandbox.lifecycle", "formal_transaction.after", "allow", ""))

    selected: list[dict[str, Any]] = []
    event_digests: dict[str, str] = {}
    remaining = list(expected)
    resolver_count = 0
    for record in relevant:
        target = record.get("target")
        scope = target.get("scope") if isinstance(target, dict) else None
        signature = (
            str(record.get("operation_class") or ""),
            str(scope or ""),
            str(record.get("decision") or ""),
            str(record.get("error_code") or ""),
        )
        if signature == ("network.request", RESOLVER_AUDIT_SCOPE, "audit_only", ""):
            resolver_count += 1
            continue
        match_index = next(
            (
                index
                for index, item in enumerate(remaining)
                if signature == (item[1], item[2], item[3], item[4])
            ),
            None,
        )
        if match_index is None:
            raise FormalEgressAuditError("formal_audit_unexpected_record")
        label, *_ = remaining.pop(match_index)
        selected.append(record)
        if not label.startswith("observation:"):
            event_digests[label] = _record_digest(record)
    if remaining or len(selected) != len(case_specs()) + 3 or set(event_digests) != {item.case_id for item in results}:
        raise FormalEgressAuditError("formal_audit_records_incomplete")
    if not 1 <= resolver_count <= 32:
        raise FormalEgressAuditError("formal_audit_resolver_binding_invalid")
    timestamps = [aggregate_security_audit._parse_timestamp(record.get("timestamp")) for record in selected]
    if timestamps != sorted(timestamps):
        raise FormalEgressAuditError("formal_audit_chronology_invalid")
    return selected, event_digests


def _transaction_projection(
    capture: formal_binding.ActiveCapture,
    *,
    audit_records_sha256: str,
) -> dict[str, str]:
    return {
        "transaction_receipt_sha256": capture.binding.transaction_receipt_sha256,
        "run_id_sha256": capture.binding.run_id_sha256,
        "sandbox_id_sha256": capture.binding.sandbox_id_sha256,
        "session_id_sha256": capture.binding.session_id_sha256,
        "policy_sha256": capture.binding.policy_sha256,
        "audit_records_sha256": audit_records_sha256,
    }


def build_evidence(
    *,
    root: Path,
    generated_at: str,
    before: formal_binding.ActiveCapture,
    after: formal_binding.ActiveCapture,
    host_component: Mapping[str, Any],
    host_component_digest: str,
    host_component_relative: str,
    results: Sequence[CaseResult],
    selected_records: Sequence[Mapping[str, Any]],
    event_digests: Mapping[str, str],
    raw_audit_sha256: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if before != after:
        raise FormalEgressAuditError("formal_binding_changed_during_probe")
    specs = case_specs()
    if len(results) != len(specs) or any(
        (
            result.case_id,
            result.decision,
            result.enforcement_layer,
            result.reason_code,
            result.observation_contract,
        )
        != (
            spec.case_id,
            spec.decision,
            spec.enforcement_layer,
            spec.reason_code,
            "controlled_receiver_no_connection" if spec.executor == "direct" else "broker_decision_receipt",
        )
        for result, spec in zip(results, specs, strict=True)
    ):
        raise FormalEgressAuditError("formal_egress_case_set_invalid")
    if len(selected_records) != len(specs) + 3 or set(event_digests) != {item.case_id for item in specs}:
        raise FormalEgressAuditError("formal_audit_records_incomplete")
    audit_records_content = b"".join(security_audit.serialize_record(record) for record in selected_records)
    if _sha256(audit_records_content) != raw_audit_sha256:
        raise FormalEgressAuditError("formal_audit_source_digest_mismatch")
    transaction = _transaction_projection(before, audit_records_sha256=raw_audit_sha256)
    runner_sha256 = _source_sha256(root, RUNNER_RELATIVE)
    egress_schema = _source_bytes(root, EGRESS_SCHEMA_RELATIVE)
    audit_schema = _source_bytes(root, AUDIT_SCHEMA_RELATIVE)
    source_set_sha256 = _canonical_sha256([raw_audit_sha256])
    cases = [
        {
            "case_id": result.case_id,
            "decision": result.decision,
            "enforcement_layer": result.enforcement_layer,
            "reason_code": result.reason_code,
            "audit_record_sha256": event_digests[result.case_id],
        }
        for result in results
    ]
    egress = {
        "schema_version": EGRESS_SCHEMA_VERSION,
        "generated_at": generated_at,
        "decision": "GO",
        "profile": PROFILE,
        "scope": "formal_business_sandbox",
        "formal_business_run": True,
        "eligible_for_completion": True,
        "host_runtime_unchanged": True,
        "cutover_performed": False,
        "transaction": transaction,
        "host_egress_component": {
            "path": host_component_relative,
            "sha256": host_component_digest,
            "schema_version": host_component["schema_version"],
            "scope": host_component["scope"],
            "decision": host_component["decision"],
            "passed": host_component["passed"],
            "eligible_for_completion": host_component["eligible_for_completion"],
            "captured_at_unix": host_component["captured_at_unix"],
            "valid_until_unix": host_component["valid_until_unix"],
        },
        "sandbox_network_enforcement": {
            "egress_mode": "broker_and_approved_providers_only",
            "direct_public_tcp_denied": True,
            "direct_public_udp_denied": True,
            "direct_public_websocket_denied": True,
            "cloud_metadata_denied": True,
            "broker_request_identity_required": True,
            "unknown_raw_socket_route_present": False,
        },
        "direct_denial_contract": {
            "receiver_binding": "verified_bridge_gateway_ephemeral_tcp_udp",
            "controlled_endpoint_permission_present": False,
            "connection_observed": False,
            "client_exit_status_only_accepted": False,
            "protocol_or_auth_failure_accepted": False,
        },
        "transfer_clients_tested": list(TRANSFER_CLIENTS),
        "cases": cases,
        "provenance": {
            "hermes_commit": HERMES_COMMIT,
            "image_sha256": before.binding.image_sha256,
            "policy_sha256": before.binding.policy_sha256,
            "mount_contract_sha256": before.binding.mount_contract_sha256,
            "runtime_config_sha256": before.binding.runtime_config_sha256,
            "egress_guard_sha256": _source_sha256(root, EGRESS_GUARD_RELATIVE),
            "request_identity_contract_sha256": _source_sha256(root, REQUEST_IDENTITY_RELATIVE),
            "evidence_schema_sha256": _sha256(egress_schema),
            "exporter_sha256": runner_sha256,
            "transaction_receipt_sha256": before.binding.transaction_receipt_sha256,
        },
        "sanitization": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "contains_local_paths": False,
            "exporter_ready": True,
        },
    }
    summary = aggregate_security_audit.aggregate_records(
        selected_records,
        source_digests=[raw_audit_sha256],
    )
    metrics = summary["metrics"]
    audit = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "decision": "GO",
        "profile": PROFILE,
        "scope": "formal_business_sandbox",
        "formal_business_run": True,
        "eligible_for_completion": True,
        "host_runtime_unchanged": True,
        "cutover_performed": False,
        "transaction": transaction,
        "source_contract": {
            "record_schema_version": security_audit.SCHEMA_VERSION,
            "aggregate_schema_version": aggregate_security_audit.SCHEMA_VERSION,
            "source_file_count": 1,
            "record_count": summary["record_count"],
            "audit_records_sha256": raw_audit_sha256,
            "source_set_sha256": source_set_sha256,
            "chronological_order_verified": True,
            "strict_schema_validated": True,
            "transaction_filtered": True,
            "single_transaction": True,
            "single_policy": True,
        },
        "identity_coverage": {
            "profile_present": True,
            "sandbox_identity_projected": True,
            "siq_run_identity_projected": True,
            "session_identity_projected": True,
            "operation_class_present": True,
            "target_projected": True,
            "decision_present": True,
            "policy_digest_present": True,
            "error_code_present": True,
            "duration_present": True,
        },
        "decision_counts": summary["decisions"],
        "operation_counts": summary["operation_classes"],
        "event_classification": {
            "formal_runner_observation_count": 3,
            "security_probe_event_count": len(specs),
            "unclassified_count": 0,
        },
        "security_case_event_sha256": [event_digests[item.case_id] for item in results],
        "metrics": {
            "policy_deny_count": metrics["policy_deny_count"],
            "audit_only_count": metrics["audit_only_count"],
            "external_upload_blocks": metrics["external_upload_blocks"],
            "immutable_write_blocks": metrics["immutable_write_blocks"],
            "sandbox_start_failures": metrics["sandbox_start_failures"],
            "gateway_overhead_ms": metrics["gateway_overhead_ms"],
        },
        "provenance": {
            "audit_contract_sha256": _source_sha256(root, AUDIT_CONTRACT_RELATIVE),
            "aggregator_sha256": _source_sha256(root, AUDIT_AGGREGATOR_RELATIVE),
            "evidence_schema_sha256": _sha256(audit_schema),
            "exporter_sha256": runner_sha256,
            "source_file_set_sha256": source_set_sha256,
            "transaction_receipt_sha256": before.binding.transaction_receipt_sha256,
        },
        "content_absence": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "contains_local_paths": False,
            "contains_request_or_response_content": False,
            "contains_sql_or_vector_payload": False,
            "contains_target_values": False,
            "contains_unprojected_session_id": False,
            "exporter_ready": True,
        },
    }
    _validate_schema(egress, egress_schema, code="formal_egress_schema_validation_failed")
    _validate_schema(audit, audit_schema, code="formal_audit_schema_validation_failed")
    if (
        egress["transaction"] != audit["transaction"]
        or audit["metrics"]["external_upload_blocks"] < 10
        or audit["operation_counts"]["network.request"] != len(specs)
        or audit["operation_counts"]["runtime.route"] != 0
        or audit["operation_counts"]["sandbox.lifecycle"] != 2
        or audit["operation_counts"]["service.preflight"] != 1
    ):
        raise FormalEgressAuditError("formal_evidence_binding_invalid")
    return egress, audit


def _markdown(title: str, *, audit: bool) -> bytes:
    if audit:
        body = (
            f"# {title}\n\n"
            "- Decision: `GO`\n"
            "- Scope: `formal_business_sandbox`\n"
            "- Source records: one running formal transaction and one policy digest\n"
            "- Fixed security cases: `17`\n"
            "- Request, response, SQL, vector, target and session values published: `false`\n"
            "- Raw audit remains in ignored private runtime state\n"
        )
    else:
        body = (
            f"# {title}\n\n"
            "- Decision: `GO`\n"
            "- Scope: `formal_business_sandbox`\n"
            "- Public GET, HEAD and bounded JSON controls: passed\n"
            "- File-shaped, transfer-client, metadata and direct public routes: denied\n"
            "- Transaction, image, policy, mounts and host runtime: unchanged\n"
            "- Request, response, target and credential material published: `false`\n"
        )
    return body.encode("ascii")


def _ensure_raw_directory(root: Path, run_id: str) -> Path:
    base = root / RAW_ROOT_RELATIVE
    current = root / "var/openshell/proofs"
    try:
        formal_binding._private_directory(current, create=False)
        if not base.exists() and not base.is_symlink():
            formal_binding._private_directory(base, create=True)
        else:
            formal_binding._private_directory(base, create=False)
        run_dir = base / run_id
        if not run_dir.exists() and not run_dir.is_symlink():
            formal_binding._private_directory(run_dir, create=True)
        else:
            formal_binding._private_directory(run_dir, create=False)
    except formal_binding.FormalFilesystemEvidenceError as exc:
        raise FormalEgressAuditError("formal_raw_directory_invalid") from exc
    return run_dir


def _artifact_paths(root: Path) -> tuple[Path, Path, Path, Path]:
    try:
        egress_json, egress_markdown = formal_binding._artifact_paths(
            root,
            EGRESS_JSON_RELATIVE,
            EGRESS_MARKDOWN_RELATIVE,
        )
        audit_json, audit_markdown = formal_binding._artifact_paths(
            root,
            AUDIT_JSON_RELATIVE,
            AUDIT_MARKDOWN_RELATIVE,
        )
    except formal_binding.FormalFilesystemEvidenceError as exc:
        raise FormalEgressAuditError(exc.code) from exc
    return egress_json, egress_markdown, audit_json, audit_markdown


def run_and_publish(
    *,
    project_root: Path,
    run_id: str,
    host_component_path: Path = HOST_COMPONENT_RELATIVE,
    timeout: int = 25,
) -> tuple[dict[str, Any], dict[str, Any], Path]:
    root = project_root.expanduser().resolve(strict=True)
    if root != REPO_ROOT:
        raise FormalEgressAuditError("project_root_invalid")
    if not formal_binding.RUN_ID_RE.fullmatch(run_id):
        raise FormalEgressAuditError("run_id_invalid")
    if not 5 <= timeout <= 60:
        raise FormalEgressAuditError("timeout_invalid")
    outputs = _artifact_paths(root)
    try:
        lock = formal_binding._runner_lock(root)
        with lock:
            before = formal_binding.capture_active_binding(project_root=root, run_id=run_id)
            gateway_ip = _validate_network_contract(before, timeout=timeout)
            captured_at = int(time.time())
            host_component, host_component_digest, host_component_relative = _read_host_component(
                root,
                host_component_path,
                now=captured_at,
            )
            audit_before = _audit_snapshot(root)
            _append_observation(
                root,
                before,
                operation_class="sandbox.lifecycle",
                scope="formal_transaction.before",
                decision="allow",
                error_code="",
                duration_ms=0,
            )
            route_duration = _run_runtime_health(before, timeout=timeout)
            _append_observation(
                root,
                before,
                operation_class="service.preflight",
                scope="formal_runtime.health",
                decision="allow",
                error_code="",
                duration_ms=route_duration,
            )
            results = [
                _run_case(before, spec, timeout=timeout, gateway_ip=gateway_ip)
                for spec in case_specs()
            ]
            after = formal_binding.capture_active_binding(project_root=root, run_id=run_id)
            _validate_network_contract(after, timeout=timeout)
            if before != after:
                raise FormalEgressAuditError("formal_binding_changed_during_probe")
            _append_observation(
                root,
                after,
                operation_class="sandbox.lifecycle",
                scope="formal_transaction.after",
                decision="allow",
                error_code="",
                duration_ms=0,
            )
            _read_host_component(root, host_component_path, now=int(time.time()))
            delta = _audit_delta(root, audit_before)
            selected, event_digests = _select_bound_records(delta, before, results)
            raw_audit = b"".join(security_audit.serialize_record(record) for record in selected)
            raw_audit_sha256 = _sha256(raw_audit)
            generated_at = _utc_now()
            egress, audit = build_evidence(
                root=root,
                generated_at=generated_at,
                before=before,
                after=after,
                host_component=host_component,
                host_component_digest=host_component_digest,
                host_component_relative=host_component_relative,
                results=results,
                selected_records=selected,
                event_digests=event_digests,
                raw_audit_sha256=raw_audit_sha256,
            )
            egress_json = json.dumps(egress, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
            audit_json = json.dumps(audit, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
            egress_markdown = _markdown("Formal OpenShell Egress Sandbox Evidence", audit=False)
            audit_markdown = _markdown("Formal OpenShell Structured Audit Evidence", audit=True)
            public_values = (
                (outputs[0], egress_json),
                (outputs[1], egress_markdown),
                (outputs[2], audit_json),
                (outputs[3], audit_markdown),
            )
            findings = []
            for path, content in public_values:
                findings.extend(check_sanitized_artifacts.scan_content(path, content))
            if findings:
                raise FormalEgressAuditError("formal_evidence_sanitization_failed")
            raw_dir = _ensure_raw_directory(root, run_id)
            raw_audit_path = raw_dir / "selected-audit.jsonl"
            raw_receipt_path = raw_dir / "receipt.json"
            raw_receipt = {
                "schema_version": RAW_SCHEMA_VERSION,
                "generated_at": generated_at,
                "runtime_identifiers": {
                    "transaction_id": before.transaction_id,
                    "run_id": before.run_id,
                    "sandbox_id": before.sandbox_id,
                    "container_id": before.container_id,
                },
                "before": asdict(before.binding),
                "after": asdict(after.binding),
                "cases": [asdict(item) for item in results],
                "selected_audit_record_count": len(selected),
                "selected_audit_sha256": raw_audit_sha256,
                "credential_material_present": False,
                "request_or_response_content_present": False,
            }
            raw_receipt_content = (
                json.dumps(raw_receipt, ensure_ascii=True, indent=2, sort_keys=True).encode("ascii") + b"\n"
            )
            try:
                formal_binding._publish_exclusive(
                    (
                        (raw_audit_path, raw_audit),
                        (raw_receipt_path, raw_receipt_content),
                        *public_values,
                    )
                )
            except formal_binding.FormalFilesystemEvidenceError as exc:
                raise FormalEgressAuditError(exc.code) from exc
            if check_sanitized_artifacts.scan_paths(list(outputs)):
                for path in (raw_audit_path, raw_receipt_path, *outputs):
                    path.unlink(missing_ok=True)
                raise FormalEgressAuditError("formal_evidence_sanitization_failed")
            return egress, audit, raw_receipt_path
    except formal_binding.FormalFilesystemEvidenceError as exc:
        raise FormalEgressAuditError(exc.code) from exc


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--host-egress-component", type=Path, default=HOST_COMPONENT_RELATIVE)
    parser.add_argument("--timeout", type=int, default=25)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        egress, audit, raw_receipt = run_and_publish(
            project_root=args.project_root,
            run_id=args.run_id,
            host_component_path=args.host_egress_component,
            timeout=args.timeout,
        )
    except (
        FormalEgressAuditError,
        LifecycleError,
        sandbox_probe.ProbeError,
        OSError,
        ValueError,
    ) as exc:
        code = exc.code if hasattr(exc, "code") else "formal_egress_audit_failed"
        print(json.dumps({"ok": False, "decision": "NO_GO", "error_code": code}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "decision": "GO",
                "egress_schema_version": egress["schema_version"],
                "audit_schema_version": audit["schema_version"],
                "case_count": len(egress["cases"]),
                "audit_record_count": audit["source_contract"]["record_count"],
                "raw_receipt_mode": f"{stat.S_IMODE(raw_receipt.stat().st_mode):04o}",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
