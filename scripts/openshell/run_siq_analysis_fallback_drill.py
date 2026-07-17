#!/usr/bin/env python3
"""Run an isolated three-request primary-503 fallback drill and publish strict evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from typing import Any, Iterator, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.openshell import (  # noqa: E402
    bridge_endpoint,
    check_sanitized_artifacts,
    check_siq_analysis_ab_prerequisites as ab_prerequisites,
    formal_fallback_drill_evidence as evidence_contract,
    formal_runtime_contract,
    gateway_runtime_identity,
    run_siq_analysis_ab_eval as ab_eval,
    siq_analysis_transaction as transaction,
)
from scripts.openshell.prepare_siq_analysis_ab_eval import PROVENANCE_SCHEMA  # noqa: E402
from scripts.openshell.siq_analysis_lifecycle import (  # noqa: E402
    BROKER_IDENTITY_SECRET_FILES,
    FALLBACK_FAULT_ENVIRONMENT,
    FALLBACK_FAULT_INJECTION_NAME,
    FORWARD_HOST,
    FORWARD_PORT,
    PROFILE,
    LifecycleAdapter,
    LifecycleError,
    _host_receipt_sha256,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ARTIFACT_ROOT_RELATIVE = Path("artifacts/openshell/v0.6")
DEFAULT_JSON = ARTIFACT_ROOT_RELATIVE / "formal-fallback-drill.sanitized.json"
DEFAULT_MARKDOWN = ARTIFACT_ROOT_RELATIVE / "formal-fallback-drill.sanitized.md"
STUB_PORT = 8004
DRILL_REPETITIONS = 3
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_REQUEST_BODY_BYTES = 16 * 1024 * 1024
API_PORT = 18081
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
FORBIDDEN_LIVE_ID_RE = re.compile(r"(?:synthetic|fixture|fake|test)", re.IGNORECASE)
ALLOWED_STUB_PATHS = frozenset({"/v1/messages", "/v1/v1/messages", "/v1/chat/completions"})


class FallbackDrillError(RuntimeError):
    """Stable failure that never contains prompt, output, credentials, or identifiers."""

    def __init__(self, code: str) -> None:
        rendered = code if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,95}", code) else "fallback_drill_failed"
        self.code = rendered
        super().__init__(rendered)


@dataclass
class StubState:
    request_count: int = 0
    invalid_request_count: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True)
class DrillInputs:
    evaluation_id: str
    dataset: ab_eval.EvaluationDataset
    summary: Mapping[str, Any]
    summary_sha256: str
    prerequisites_sha256: str
    provenance: Mapping[str, Any]
    provenance_sha256: str


def _sha256(content: bytes | str) -> str:
    return hashlib.sha256(content.encode("utf-8") if isinstance(content, str) else content).hexdigest()


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = child
    return value


def _stable_file(path: Path, *, private: bool, maximum: int = MAX_JSON_BYTES) -> bytes:
    descriptor = -1
    try:
        expected = path.lstat()
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            stat.S_ISLNK(expected.st_mode)
            or not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or (private and stat.S_IMODE(opened.st_mode) != 0o600)
            or not 0 < opened.st_size <= maximum
            or (expected.st_dev, expected.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise FallbackDrillError("fallback_drill_input_invalid")
        content = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, maximum + 1 - len(content))):
            content.extend(chunk)
            if len(content) > maximum:
                raise FallbackDrillError("fallback_drill_input_invalid")
        finished = os.fstat(descriptor)
        final = path.lstat()
        identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
        if identity != (
            finished.st_dev,
            finished.st_ino,
            finished.st_size,
            finished.st_mtime_ns,
            finished.st_ctime_ns,
        ) or identity != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns):
            raise FallbackDrillError("fallback_drill_input_changed")
        return bytes(content)
    except FallbackDrillError:
        raise
    except OSError as exc:
        raise FallbackDrillError("fallback_drill_input_invalid") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _json_file(path: Path, *, private: bool, maximum: int = MAX_JSON_BYTES) -> tuple[Mapping[str, Any], bytes]:
    content = _stable_file(path, private=private, maximum=maximum)
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise FallbackDrillError("fallback_drill_input_json_invalid") from exc
    if not isinstance(payload, dict):
        raise FallbackDrillError("fallback_drill_input_json_invalid")
    return payload, content


def load_inputs(
    *,
    evaluation_id: str,
    dataset_path: Path,
    summary_path: Path,
    prerequisites_path: Path,
    provenance_path: Path,
) -> DrillInputs:
    if not SAFE_ID_RE.fullmatch(evaluation_id) or FORBIDDEN_LIVE_ID_RE.search(evaluation_id):
        raise FallbackDrillError("fallback_drill_evaluation_id_invalid")
    dataset_payload, dataset_content = _json_file(dataset_path, private=True, maximum=ab_eval.MAX_DATASET_BYTES)
    summary, summary_content = _json_file(summary_path, private=True)
    prerequisites, prerequisites_content = _json_file(prerequisites_path, private=True, maximum=1024 * 1024)
    provenance, provenance_content = _json_file(provenance_path, private=True, maximum=1024 * 1024)
    try:
        dataset = ab_eval.parse_dataset(dataset_payload, sha256=_sha256(dataset_content))
    except ab_eval.EvaluationConfigurationError as exc:
        raise FallbackDrillError("fallback_drill_dataset_invalid") from exc
    arms = provenance.get("arms")
    openshell = arms.get("openshell") if isinstance(arms, dict) else None
    attestation = provenance.get("runtime_attestation")
    prerequisite_provenance = prerequisites.get("provenance")
    if (
        dataset.profile != PROFILE
        or any(case.expectations.fallback_expected is not None for case in dataset.cases)
        or any(case.expectations.policy_denial_expected for case in dataset.cases)
        or prerequisites.get("schema_version") != ab_prerequisites.SCHEMA_VERSION
        or prerequisites.get("decision") != "GO"
        or prerequisites.get("evaluation_id") != evaluation_id
        or prerequisites.get("network_probe_performed") is not True
        or not isinstance(prerequisites.get("dataset"), dict)
        or prerequisites["dataset"].get("sha256") != dataset.sha256
        or not isinstance(prerequisite_provenance, dict)
        or prerequisite_provenance.get("schema_version") != PROVENANCE_SCHEMA
        or prerequisite_provenance.get("sha256") != _sha256(provenance_content)
        or prerequisite_provenance.get("host_runtime_verified") is not True
        or prerequisite_provenance.get("host_candidate_source_match") is not True
        or summary.get("schema_version") != ab_eval.SUMMARY_SCHEMA_VERSION
        or summary.get("evaluation_id") != evaluation_id
        or summary.get("dataset_sha256") != dataset.sha256
        or summary.get("model") != dataset.model
        or summary.get("temperature") != dataset.temperature
        or not isinstance(summary.get("quality_gate"), dict)
        or summary["quality_gate"].get("passed") is not True
        or summary["quality_gate"].get("failure_reasons") != []
        or summary.get("prerequisites_sha256") != _sha256(prerequisites_content)
        or provenance.get("schema_version") != PROVENANCE_SCHEMA
        or provenance.get("evaluation_id") != evaluation_id
        or provenance.get("dataset_sha256") != dataset.sha256
        or not isinstance(openshell, dict)
        or not isinstance(attestation, dict)
        or openshell.get("runtime") != "openshell"
        or not isinstance(openshell.get("mount_contract_sha256"), str)
        or attestation.get("primary_provider") != "minimax-cn"
        or attestation.get("primary_model") != dataset.model
        or attestation.get("arms_match") is not True
    ):
        raise FallbackDrillError("fallback_drill_inputs_not_release_quality")
    for arm_name in ("host", "openshell"):
        arm = summary.get("arms", {}).get(arm_name) if isinstance(summary.get("arms"), dict) else None
        if (
            not isinstance(arm, dict)
            or arm.get("fallback_expected_execution_count") != 0
            or arm.get("fallback_telemetry_expected_count") != 0
            or arm.get("fallback_success_rate") is not None
            or arm.get("fallback_telemetry_coverage") is not None
            or arm.get("unexpected_fallback_count") != 0
            or arm.get("policy_false_positive_rate") != 0
        ):
            raise FallbackDrillError("fallback_drill_normal_summary_invalid")
    return DrillInputs(
        evaluation_id=evaluation_id,
        dataset=dataset,
        summary=summary,
        summary_sha256=_sha256(summary_content),
        prerequisites_sha256=_sha256(prerequisites_content),
        provenance=provenance,
        provenance_sha256=_sha256(provenance_content),
    )


def _stub_handler(state: StubState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _finish(self, status: int, payload: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            length_value = self.headers.get("Content-Length")
            try:
                length = int(length_value or "0")
            except ValueError:
                length = -1
            valid = self.path in ALLOWED_STUB_PATHS and 0 <= length <= MAX_REQUEST_BODY_BYTES
            if valid:
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(64 * 1024, remaining))
                    if not chunk:
                        valid = False
                        break
                    remaining -= len(chunk)
            with state.lock:
                if valid:
                    state.request_count += 1
                else:
                    state.invalid_request_count += 1
            if not valid:
                self._finish(400, b'{"error":"invalid_request"}')
                return
            self._finish(503, b'{"error":{"type":"overloaded_error","message":"temporary"}}')

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            with state.lock:
                state.invalid_request_count += 1
            self._finish(405, b'{"error":"method_not_allowed"}')

    return Handler


@contextmanager
def primary_503_stub(bind_host: str) -> Iterator[StubState]:
    state = StubState()
    try:
        server = ThreadingHTTPServer((bind_host, STUB_PORT), _stub_handler(state), bind_and_activate=True)
    except OSError as exc:
        raise FallbackDrillError("fallback_stub_bind_failed") from exc
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="siq-fallback-503", daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        if thread.is_alive():
            raise FallbackDrillError("fallback_stub_cleanup_failed")


def _listener_pids(port: int) -> set[int]:
    try:
        result = subprocess.run(
            ["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise FallbackDrillError("fallback_listener_probe_failed") from exc
    if result.returncode not in {0, 1}:
        raise FallbackDrillError("fallback_listener_probe_failed")
    return {int(value) for value in result.stdout.splitlines() if value.isdigit() and int(value) > 1}


def _api_runtime_receipt(root: Path) -> dict[str, Any]:
    pids = _listener_pids(API_PORT)
    if len(pids) != 1:
        raise FallbackDrillError("fallback_api_runtime_identity_invalid")
    pid = next(iter(pids))
    proc = Path("/proc") / str(pid)
    try:
        proc_info = proc.stat()
        raw_cmdline = (proc / "cmdline").read_bytes()
        command = [item.decode("utf-8", errors="strict") for item in raw_cmdline.split(b"\0") if item]
        stat_text = (proc / "stat").read_text(encoding="ascii")
        environment = (proc / "environ").read_bytes()
        executable = (proc / "exe").resolve(strict=True)
        cwd = (proc / "cwd").resolve(strict=True)
    except (OSError, UnicodeError) as exc:
        raise FallbackDrillError("fallback_api_runtime_identity_invalid") from exc
    expected_command = [
        str(root / "apps/api/.venv/bin/python3"),
        "-m",
        "uvicorn",
        "main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(API_PORT),
        "--no-access-log",
    ]
    close = stat_text.rfind(")")
    fields = stat_text[close + 2 :].split() if close > 0 else []
    runtime_values: list[str] = []
    for entry in environment.split(b"\0"):
        key, separator, value = entry.partition(b"=")
        if separator and key == b"SIQ_HERMES_RUNTIME":
            try:
                runtime_values.append(value.decode("ascii"))
            except UnicodeError as exc:
                raise FallbackDrillError("fallback_api_runtime_identity_invalid") from exc
    runtime_source = "environment" if runtime_values else "application_default"
    runtime_value = runtime_values[0] if runtime_values else "host"
    if (
        proc_info.st_uid != os.geteuid()
        or command != expected_command
        or cwd != root / "apps/api"
        or len(fields) <= 19
        or not fields[19].isdigit()
        or len(runtime_values) > 1
        or runtime_value != "host"
        or not executable.is_file()
    ):
        raise FallbackDrillError("fallback_api_runtime_identity_invalid")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f"http://127.0.0.1:{API_PORT}/health", method="GET")
    try:
        with opener.open(request, timeout=2) as response:
            body = response.read(64 * 1024 + 1)
            status = response.status
        health = json.loads(body)
    except (OSError, urllib.error.URLError, UnicodeError, json.JSONDecodeError) as exc:
        raise FallbackDrillError("fallback_api_runtime_health_invalid") from exc
    if status != 200 or len(body) > 64 * 1024 or not isinstance(health, dict) or health.get("status") != "ok":
        raise FallbackDrillError("fallback_api_runtime_health_invalid")
    return {
        "schema_version": "siq.openshell.api-runtime-receipt.v1",
        "pid": pid,
        "start_ticks": int(fields[19]),
        "cmdline_sha256": _sha256(raw_cmdline),
        "executable_sha256": _sha256(executable.read_bytes()),
        "cwd_sha256": _sha256(str(cwd)),
        "runtime": runtime_value,
        "runtime_source": runtime_source,
        "port": API_PORT,
        "health_status_ok": True,
    }


def _terminal_resources_valid(resources: Any) -> bool:
    if not isinstance(resources, dict) or set(resources) != set(transaction.FORMAL_RESOURCES):
        return False
    run_dir = resources.get("run_dir")
    if (
        not isinstance(run_dir, dict)
        or run_dir.get("kind") != "directory"
        or run_dir.get("disposition") != "retain"
        or run_dir.get("state") != "present"
    ):
        return False
    return all(
        isinstance(resources.get(name), dict)
        and resources[name].get("kind") == transaction.FORMAL_RESOURCES[name]
        and resources[name].get("disposition") == "remove"
        and resources[name].get("state") == "removed"
        for name in ("guard", "secrets", "sandbox", "forward")
    )


def _validate_terminal_cleanup(
    *,
    root: Path,
    adapter: LifecycleAdapter,
    spec: Any,
    terminal: Mapping[str, Any],
) -> Mapping[str, Any]:
    loaded_spec, manifest = adapter._load_manifest(spec.run_id)
    resources = terminal.get("resources")
    if (
        loaded_spec != spec
        or terminal.get("phase") != "stopped"
        or terminal.get("terminal_action") != "stop"
        or terminal.get("error_code") != ""
        or manifest.get("phase") != "stopped"
        or manifest.get("error_code") != ""
        or not _terminal_resources_valid(resources)
    ):
        raise FallbackDrillError("fallback_terminal_cleanup_invalid")
    try:
        adapter._verify_transaction_receipts(terminal, spec, manifest)
        if (root / transaction.ACTIVE_RELATIVE).exists() or (root / transaction.ACTIVE_RELATIVE).is_symlink():
            raise FallbackDrillError("fallback_terminal_cleanup_invalid")
        if [item for item in adapter._sandbox_inventory() if item.get("name") == spec.sandbox_name]:
            raise FallbackDrillError("fallback_terminal_cleanup_invalid")
        if adapter._docker_container_ids(spec.sandbox_name):
            raise FallbackDrillError("fallback_terminal_cleanup_invalid")
        if not adapter.backend.port_listener_absent(FORWARD_HOST, FORWARD_PORT):
            raise FallbackDrillError("fallback_terminal_cleanup_invalid")
        for name in ("api.key", "run.nonce", *BROKER_IDENTITY_SECRET_FILES):
            path = spec.run_dir / name
            if path.exists() or path.is_symlink():
                raise FallbackDrillError("fallback_terminal_cleanup_invalid")
        for resource in ("guard", "forward"):
            process = adapter._read_process(spec, f"{resource}.process.json", resource)
            if adapter.backend.process_snapshot(process.pid, resource) is not None:
                raise FallbackDrillError("fallback_terminal_cleanup_invalid")
        if adapter._sandbox_receipt_sha(spec, manifest) != resources["sandbox"]["receipt_sha256"]:
            raise FallbackDrillError("fallback_terminal_cleanup_invalid")
    except LifecycleError as exc:
        raise FallbackDrillError("fallback_terminal_cleanup_invalid") from exc
    return manifest


def summarize_observations(
    observations: Sequence[ab_eval.RunObservation],
    *,
    primary_provider: str,
    primary_model: str,
) -> dict[str, Any]:
    configured_providers = {item.configured_provider for item in observations if item.configured_provider}
    configured_models = {item.configured_model for item in observations if item.configured_model}
    effective_providers = {item.effective_provider for item in observations if item.effective_provider}
    effective_models = {item.effective_model for item in observations if item.effective_model}
    completed = sum(item.status == "completed" for item in observations)
    telemetry = sum(
        item.fallback_activated is not None
        and item.configured_provider is not None
        and item.configured_model is not None
        and item.effective_provider is not None
        and item.effective_model is not None
        for item in observations
    )
    activated = sum(item.fallback_activated is True for item in observations)
    contract_failures = sum(
        not (item.create_contract_ok and item.sse_contract_ok and item.terminal_contract_ok) for item in observations
    )
    silent_failures = sum(
        item.status == "completed"
        and (
            item.fallback_activated is not True
            or item.effective_provider == item.configured_provider
            or item.effective_model == item.configured_model
        )
        for item in observations
    )
    result = {
        "execution_count": len(observations),
        "completed_count": completed,
        "telemetry_count": telemetry,
        "fallback_activated_count": activated,
        "configured_provider": next(iter(configured_providers), ""),
        "configured_model": next(iter(configured_models), ""),
        "effective_providers": sorted(effective_providers),
        "effective_models": sorted(effective_models),
        "silent_failure_count": silent_failures,
        "policy_denial_count": sum(item.policy_denied for item in observations),
        "contract_failure_count": contract_failures,
        "timeout_count": sum(item.status == "timed_out" for item in observations),
    }
    if (
        len(observations) != DRILL_REPETITIONS
        or configured_providers != {primary_provider}
        or configured_models != {primary_model}
        or primary_provider in effective_providers
        or primary_model in effective_models
        or any(result[field] != DRILL_REPETITIONS for field in ("completed_count", "telemetry_count", "fallback_activated_count"))
        or any(
            result[field] != 0
            for field in ("silent_failure_count", "policy_denial_count", "contract_failure_count", "timeout_count")
        )
    ):
        raise FallbackDrillError("fallback_drill_observations_failed")
    return result


def _run_requests(inputs: DrillInputs, *, key: str) -> tuple[list[ab_eval.RunObservation], dict[str, Any]]:
    attestation = inputs.provenance["runtime_attestation"]
    client = ab_eval.RunsClient(runs_url=f"http://{FORWARD_HOST}:{FORWARD_PORT}/v1/runs", api_key=key)
    observations: list[ab_eval.RunObservation] = []
    for repetition in range(1, DRILL_REPETITIONS + 1):
        payload = {
            "model": inputs.dataset.model,
            "temperature": inputs.dataset.temperature,
            "instructions": "Execute the isolated fallback telemetry drill and return a short acknowledgement.",
            "input": "Return FALLBACK_DRILL_OK.",
            "conversation_history": [],
            "session_id": f"fallback-drill-{inputs.evaluation_id}-{repetition}",
        }
        observations.append(client.execute(payload, timeout_seconds=min(inputs.dataset.run_timeout_seconds, 600)))
    return observations, summarize_observations(
        observations,
        primary_provider=str(attestation["primary_provider"]),
        primary_model=str(attestation["primary_model"]),
    )


def _output_path(root: Path, value: Path, *, suffix: str) -> Path:
    rendered = PurePosixPath(value.as_posix())
    if rendered.is_absolute() or any(part in {"", ".", ".."} for part in rendered.parts):
        raise FallbackDrillError("fallback_output_path_invalid")
    path = root.joinpath(*rendered.parts)
    if path.parent != root / ARTIFACT_ROOT_RELATIVE or not path.name.endswith(suffix):
        raise FallbackDrillError("fallback_output_path_invalid")
    if path.exists() or path.is_symlink():
        raise FallbackDrillError("fallback_output_exists")
    return path


def _write_exclusive(path: Path, content: bytes) -> None:
    descriptor = -1
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise FallbackDrillError("fallback_output_write_failed")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise FallbackDrillError("fallback_output_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _publish(root: Path, evidence: Mapping[str, Any], json_relative: Path, markdown_relative: Path) -> tuple[Path, Path]:
    json_path = _output_path(root, json_relative, suffix=".sanitized.json")
    markdown_path = _output_path(root, markdown_relative, suffix=".sanitized.md")
    if json_path.name.removesuffix(".sanitized.json") != markdown_path.name.removesuffix(".sanitized.md"):
        raise FallbackDrillError("fallback_output_pair_invalid")
    json_content = (json.dumps(evidence, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("ascii")
    markdown = (
        "# Formal siq_analysis Fallback Drill\n\n"
        "- Decision: `PASS`\n"
        "- Fault: isolated primary HTTP 503\n"
        "- Executions: `3`\n"
        "- Fallback telemetry and successful completion: `3/3`\n"
        "- Policy denials and silent failures: `0`\n"
        "- Sandbox, forward listener and stub: removed\n"
        "- Host identity and default runtime: unchanged\n"
    ).encode("ascii")
    created: list[Path] = []
    try:
        _write_exclusive(json_path, json_content)
        created.append(json_path)
        _write_exclusive(markdown_path, markdown)
        created.append(markdown_path)
        if check_sanitized_artifacts.scan_paths(created):
            raise FallbackDrillError("fallback_output_sanitization_failed")
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        raise
    return json_path, markdown_path


def execute_drill(
    *,
    project_root: Path,
    company: str,
    inputs: DrillInputs,
) -> dict[str, Any]:
    root = project_root.resolve(strict=True)
    adapter = LifecycleAdapter(project_root=root)
    acquire = getattr(adapter.backend, "acquire_maintenance_lock", None)
    if not callable(acquire):
        raise FallbackDrillError("fallback_maintenance_lock_unavailable")
    acquire(timeout_seconds=60)
    if (root / transaction.ACTIVE_RELATIVE).exists() or _listener_pids(FORWARD_PORT):
        raise FallbackDrillError("fallback_formal_runtime_conflict")
    if _listener_pids(STUB_PORT):
        raise FallbackDrillError("fallback_stub_port_in_use")
    bridge = bridge_endpoint.discover_bridge_endpoint()
    host_before = adapter._stable_host_receipt()
    gateway_before = gateway_runtime_identity.verify_runtime_identity(root)
    api_runtime_before = _api_runtime_receipt(root)
    run_id = "fallback-" + os.urandom(6).hex()
    spec = adapter.prepare_analysis_root_for_start(
        profile=PROFILE,
        market="cn",
        company=company,
        run_id=run_id,
    )
    started = False
    start_result: Mapping[str, Any] | None = None
    running_manifest: Mapping[str, Any] | None = None
    mount_contract: Mapping[str, Any] | None = None
    results: Mapping[str, Any] | None = None
    state: StubState | None = None
    stop_error: Exception | None = None
    try:
        with primary_503_stub(bridge.gateway_ip) as active_state:
            state = active_state
            start_result = adapter.start(spec, sandbox_environment_overrides=FALLBACK_FAULT_ENVIRONMENT)
            started = True
            running_manifest = json.loads((spec.run_dir / "run.json").read_bytes())
            openshell_provenance = inputs.provenance["arms"]["openshell"]
            mount_contract = formal_runtime_contract.normalized_mount_contract(
                project_root=root,
                mount_plan=root / str(running_manifest.get("mount_plan") or ""),
                analysis_root=spec.analysis_root,
                runtime_snapshot=root / str(running_manifest.get("runtime_snapshot") or ""),
            )
            if (
                running_manifest.get("phase") != "running"
                or running_manifest.get("image_id") != openshell_provenance.get("image_id")
                or running_manifest.get("policy_sha256") != openshell_provenance.get("policy_sha256")
                or mount_contract.get("mount_contract_sha256") != openshell_provenance.get("mount_contract_sha256")
            ):
                raise FallbackDrillError("fallback_runtime_provenance_mismatch")
            key = ab_eval.load_api_key(spec.run_dir / "api.key")
            _observations, results = _run_requests(inputs, key=key)
    finally:
        if started:
            try:
                adapter.stop(profile=PROFILE, run_id=run_id)
            except Exception as exc:  # cleanup failure must override any apparent drill success
                stop_error = exc
    if stop_error is not None:
        raise FallbackDrillError("fallback_lifecycle_cleanup_failed") from stop_error
    if start_result is None or running_manifest is None or mount_contract is None or results is None or state is None:
        raise FallbackDrillError("fallback_drill_incomplete")
    if state.invalid_request_count != 0 or state.request_count < DRILL_REPETITIONS:
        raise FallbackDrillError("fallback_stub_observation_invalid")
    if _listener_pids(STUB_PORT) or _listener_pids(FORWARD_PORT):
        raise FallbackDrillError("fallback_listener_cleanup_failed")
    terminal = transaction.load(root, str(start_result["transaction_id"]))
    terminal_manifest = _validate_terminal_cleanup(root=root, adapter=adapter, spec=spec, terminal=terminal)
    host_after = adapter._stable_host_receipt(after_stop=True)
    gateway_after = gateway_runtime_identity.verify_runtime_identity(root)
    api_runtime_after = _api_runtime_receipt(root)
    if (
        host_after != host_before
        or gateway_after != gateway_before
        or api_runtime_after != api_runtime_before
        or terminal_manifest.get("sandbox_id") != running_manifest.get("sandbox_id")
        or terminal_manifest.get("container_id") != running_manifest.get("container_id")
    ):
        raise FallbackDrillError("fallback_terminal_cleanup_invalid")
    transaction_value = {
        "transaction_receipt_sha256": formal_runtime_contract.canonical_sha256(terminal),
        "run_id_sha256": _sha256(run_id),
        "sandbox_id_sha256": _sha256(str(running_manifest["sandbox_id"])),
        "container_id_sha256": _sha256(str(running_manifest["container_id"])),
        "host_receipt_before_sha256": _host_receipt_sha256(host_before),
        "host_receipt_after_sha256": _host_receipt_sha256(host_after),
        "api_runtime_receipt_before_sha256": formal_runtime_contract.canonical_sha256(api_runtime_before),
        "api_runtime_receipt_after_sha256": formal_runtime_contract.canonical_sha256(api_runtime_after),
        "gateway_receipt_before_sha256": formal_runtime_contract.canonical_sha256(gateway_before),
        "gateway_receipt_after_sha256": formal_runtime_contract.canonical_sha256(gateway_after),
        "image_id": str(running_manifest["image_id"]),
        "policy_sha256": str(running_manifest["policy_sha256"]),
        "mount_plan_sha256": str(running_manifest["mount_plan_sha256"]),
        "mount_contract_sha256": str(mount_contract["mount_contract_sha256"]),
        "runtime_config_sha256": str(inputs.provenance["arms"]["openshell"]["runtime_config_sha256"]),
        "fault_injection_sha256": _sha256(_stable_file(spec.run_dir / FALLBACK_FAULT_INJECTION_NAME, private=True)),
    }
    evidence = {
        "schema_version": evidence_contract.SCHEMA_VERSION,
        "generated_at": _utc_now(),
        "decision": "PASS",
        "profile": PROFILE,
        "evaluation_id": inputs.evaluation_id,
        "dataset_sha256": inputs.dataset.sha256,
        "normal_summary_sha256": inputs.summary_sha256,
        "prerequisites_sha256": inputs.prerequisites_sha256,
        "provenance_sha256": inputs.provenance_sha256,
        "transaction": transaction_value,
        "fault_injection": {
            "kind": "primary_http_503",
            "bind_scope": "verified_docker_bridge_gateway_only",
            "bind_port": STUB_PORT,
            "expected_status": 503,
            "target_url_sha256": _sha256(FALLBACK_FAULT_ENVIRONMENT["MINIMAX_CN_BASE_URL"]),
            "stub_request_count": state.request_count,
            "activated_for_sandbox_only": True,
            "credential_values_persisted": False,
            "request_headers_persisted": False,
            "request_body_persisted": False,
            "response_body_persisted": False,
        },
        "results": dict(results),
        "cleanup": {
            "sandbox_removed": True,
            "container_removed": True,
            "forward_listener_removed": True,
            "stub_listener_removed": True,
            "temporary_secret_files_removed": True,
            "host_listener_identity_unchanged": True,
            "default_route_unchanged": True,
            "production_gateway_untouched": True,
            "residual_process_count": 0,
            "residual_listener_count": 0,
        },
        "provenance": {
            "evidence_schema_sha256": evidence_contract.source_sha256(root, evidence_contract.SCHEMA_RELATIVE),
            "runner_sha256": evidence_contract.source_sha256(root, evidence_contract.RUNNER_RELATIVE),
            "validator_sha256": evidence_contract.source_sha256(root, evidence_contract.VALIDATOR_RELATIVE),
            "lifecycle_sha256": evidence_contract.source_sha256(root, evidence_contract.LIFECYCLE_RELATIVE),
            "evaluator_sha256": evidence_contract.source_sha256(root, evidence_contract.EVALUATOR_RELATIVE),
            "primary_provider": str(inputs.provenance["runtime_attestation"]["primary_provider"]),
            "primary_model": str(inputs.provenance["runtime_attestation"]["primary_model"]),
            "fallback_route_sha256": str(inputs.provenance["runtime_attestation"]["fallback_route_sha256"]),
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
    evidence_contract.validate_bindings(
        evidence,
        root=root,
        normal_summary=inputs.summary,
        normal_summary_sha256=inputs.summary_sha256,
        prerequisites_sha256=inputs.prerequisites_sha256,
        provenance_report=inputs.provenance,
        provenance_sha256=inputs.provenance_sha256,
    )
    return evidence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument("--company", required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--normal-summary", type=Path, required=True)
    parser.add_argument("--prerequisites", type=Path, required=True)
    parser.add_argument("--provenance", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--output-markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--confirm-live-drill", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if not args.confirm_live_drill:
            raise FallbackDrillError("fallback_live_drill_not_confirmed")
        root = args.project_root.resolve(strict=True)
        inputs = load_inputs(
            evaluation_id=args.evaluation_id,
            dataset_path=args.dataset,
            summary_path=args.normal_summary,
            prerequisites_path=args.prerequisites,
            provenance_path=args.provenance,
        )
        evidence = execute_drill(project_root=root, company=args.company, inputs=inputs)
        json_path, markdown_path = _publish(root, evidence, args.output_json, args.output_markdown)
        print(
            json.dumps(
                {
                    "ok": True,
                    "decision": "PASS",
                    "json": json_path.relative_to(root).as_posix(),
                    "markdown": markdown_path.relative_to(root).as_posix(),
                },
                sort_keys=True,
            )
        )
        return 0
    except (
        FallbackDrillError,
        evidence_contract.FallbackEvidenceError,
        LifecycleError,
        bridge_endpoint.BridgeEndpointError,
        gateway_runtime_identity.GatewayRuntimeError,
        transaction.TransactionError,
        formal_runtime_contract.FormalRuntimeContractError,
        OSError,
        ValueError,
        TypeError,
    ) as exc:
        code = getattr(exc, "code", "fallback_drill_failed")
        print(json.dumps({"ok": False, "error_code": code}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
