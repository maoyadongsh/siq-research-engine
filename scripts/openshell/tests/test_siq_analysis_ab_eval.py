from __future__ import annotations

import copy
import hashlib
import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from scripts.openshell import (
    check_sanitized_artifacts,
    export_sanitized_evidence,
    run_siq_analysis_ab_eval as module,
)

HOST_KEY = "host-eval-key-000000000000"
OPENSHELL_KEY = "openshell-eval-key-00000000"
INSTRUCTIONS = "SYNTHETIC_SECRET_INSTRUCTIONS_DO_NOT_PERSIST"
CASE_ONE_INPUT = "SYNTHETIC_SECRET_CASE_ONE_DO_NOT_PERSIST"
CASE_TWO_INPUT = "SYNTHETIC_SECRET_CASE_TWO_DO_NOT_PERSIST"
CASE_ONE_OUTPUT = "# Executive Summary\n\nValue 42 [CIT-1] EVID-1"
CASE_TWO_OUTPUT = "# Risk\n\nInsufficient evidence for a supported conclusion."


def test_scoring_semantics_use_new_private_artifact_schemas() -> None:
    assert module.RAW_SCHEMA_VERSION == "siq.openshell.siq-analysis-ab-raw.v2"
    assert module.SUMMARY_SCHEMA_VERSION == "siq.openshell.siq-analysis-ab-summary.v3"


def _expectations(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "numeric": [],
        "citations": [],
        "evidence_ids": [],
        "required_sections": [],
        "abstention_required": False,
        "abstention_markers": [],
        "required_tools": [],
        "fallback_expected": False,
        "policy_denial_expected": False,
    }
    payload.update(overrides)
    return payload


def _dataset_payload() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    for index in range(5):
        cases.append(
            {
                "case_id": f"quality-1-{index + 1}",
                "input": CASE_ONE_INPUT,
                "history": [{"role": "user", "content": "SYNTHETIC_SECRET_HISTORY_DO_NOT_PERSIST"}],
                "expectations": _expectations(
                    numeric=[{"expectation_id": "answer", "value": 42, "absolute_tolerance": 0}],
                    citations=["[CIT-1]"],
                    evidence_ids=["EVID-1"],
                    required_sections=["Executive Summary"],
                    required_tools=["pg_query"],
                ),
            }
        )
    for index in range(5):
        cases.append(
            {
                "case_id": f"quality-2-{index + 1}",
                "input": CASE_TWO_INPUT,
                "history": [],
                "expectations": _expectations(
                    required_sections=["Risk"],
                    abstention_required=True,
                    abstention_markers=["insufficient evidence"],
                    fallback_expected=True,
                ),
            }
        )
    return {
        "schema_version": module.DATASET_SCHEMA_VERSION,
        "profile": "siq_analysis",
        "model": "siq-eval-model",
        "temperature": 0.1,
        "instructions": INSTRUCTIONS,
        "repetitions": 3,
        "run_timeout_seconds": 2,
        "cases": cases,
    }


class TickClock:
    """Make latency comparisons deterministic without changing HTTP behavior."""

    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.001
        return self.value


@dataclass
class ServerState:
    arm: str
    key: str
    behavior: str = "normal"
    shared_order: list[tuple[str, dict[str, Any]]] | None = None
    payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    received: list[dict[str, Any]] = field(default_factory=list)
    stop_calls: list[str] = field(default_factory=list)
    auth_failures: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


def _write_json(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    content = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(content)))
    handler.send_header("Connection", "close")
    handler.end_headers()
    handler.wfile.write(content)


def _handler_for(state: ServerState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _authorized(self) -> bool:
            if self.headers.get("Authorization") == f"Bearer {state.key}":
                return True
            with state.lock:
                state.auth_failures += 1
            _write_json(self, 401, {"error": "unauthorized"})
            return False

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            if not self._authorized():
                return
            if self.path == "/v1/runs":
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                with state.lock:
                    index = len(state.received) + 1
                    run_id = f"run_{state.arm}_{index}"
                    state.received.append(copy.deepcopy(payload))
                    state.payloads[run_id] = copy.deepcopy(payload)
                    if state.shared_order is not None:
                        state.shared_order.append((state.arm, copy.deepcopy(payload)))
                if state.behavior == "redirect_create":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:9/capture")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                elif state.behavior == "bad_create_status":
                    _write_json(self, 200, {"run_id": run_id, "status": "started"})
                else:
                    _write_json(self, 202, {"run_id": run_id, "status": "started"})
                return
            if self.path.endswith("/stop"):
                run_id = self.path.removeprefix("/v1/runs/").removesuffix("/stop")
                with state.lock:
                    state.stop_calls.append(run_id)
                if state.behavior == "bad_stop_contract":
                    _write_json(self, 200, {"run_id": run_id, "status": "stopped"})
                else:
                    _write_json(self, 200, {"run_id": run_id, "status": "stopping"})
                return
            _write_json(self, 404, {"error": "not_found"})

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
            if not self._authorized():
                return
            prefix = "/v1/runs/"
            suffix = "/events"
            if not self.path.startswith(prefix) or not self.path.endswith(suffix):
                _write_json(self, 404, {"error": "not_found"})
                return
            run_id = self.path[len(prefix) : -len(suffix)]
            with state.lock:
                payload = copy.deepcopy(state.payloads.get(run_id))
            if payload is None:
                _write_json(self, 404, {"error": "run_not_found"})
                return
            if state.behavior == "bad_sse_content_type":
                _write_json(self, 200, {"event": "not_sse"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            if state.behavior in {"timeout", "bad_stop_contract"}:
                time.sleep(0.25)
                return
            if state.behavior == "heartbeat_then_stall":
                time.sleep(0.07)
                self.wfile.write(b": keepalive\n\n")
                self.wfile.flush()
                time.sleep(0.25)
                return
            if payload["input"] == CASE_ONE_INPUT:
                output = CASE_ONE_OUTPUT
                tool_events = [
                    {"event": "tool.started", "run_id": run_id, "tool": "pg_query"},
                    {"event": "tool.completed", "run_id": run_id, "tool": "pg_query", "error": False},
                ]
                if state.behavior == "tool_failure_then_success":
                    tool_events = [
                        {"event": "tool.started", "run_id": run_id, "tool": "pg_query"},
                        {"event": "tool.completed", "run_id": run_id, "tool": "pg_query", "error": True},
                        {"event": "tool.started", "run_id": run_id, "tool": "pg_query"},
                        {"event": "tool.completed", "run_id": run_id, "tool": "pg_query", "error": False},
                    ]
                elif state.behavior == "tool_success_then_failure":
                    tool_events = [
                        {"event": "tool.started", "run_id": run_id, "tool": "pg_query"},
                        {"event": "tool.completed", "run_id": run_id, "tool": "pg_query", "error": False},
                        {"event": "tool.started", "run_id": run_id, "tool": "pg_query"},
                        {"event": "tool.completed", "run_id": run_id, "tool": "pg_query", "error": True},
                    ]
                events = [
                    {"event": "message.delta", "run_id": run_id, "delta": output},
                    *tool_events,
                    {
                        "event": "run.completed",
                        "run_id": run_id,
                        "output": output,
                        "runtime": None if state.behavior == "missing_runtime" else _runtime(False),
                    },
                ]
            else:
                output = CASE_TWO_OUTPUT
                events = [
                    {"event": "message.delta", "run_id": run_id, "delta": output},
                    {
                        "event": "run.completed",
                        "run_id": run_id,
                        "output": output,
                        "runtime": None if state.behavior == "missing_runtime" else _runtime(True),
                    },
                ]
            for event in events:
                self.wfile.write(b"data: " + json.dumps(event, separators=(",", ":")).encode() + b"\n\n")
                self.wfile.flush()

    return Handler


def _runtime(fallback: bool) -> dict[str, Any]:
    return {
        "schema_version": "hermes.run_runtime.v1",
        "requested_model": "siq-eval-model",
        "configured": {"provider": "synthetic", "model": "siq-eval-model"},
        "effective": {
            "provider": "synthetic-fallback" if fallback else "synthetic",
            "model": "siq-eval-model-fallback" if fallback else "siq-eval-model",
        },
        "fallback": {"activated": fallback},
    }


@contextmanager
def _server(state: ServerState) -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(state))
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1/runs"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _write_key(path: Path, key: str, mode: int = 0o600) -> Path:
    path.write_text(key + "\n", encoding="utf-8")
    path.chmod(mode)
    return path


def test_full_interleaved_ab_contract_and_sanitized_artifacts(tmp_path: Path) -> None:
    order: list[tuple[str, dict[str, Any]]] = []
    host_state = ServerState("host", HOST_KEY, shared_order=order)
    openshell_state = ServerState("openshell", OPENSHELL_KEY, shared_order=order)
    dataset_payload = _dataset_payload()
    dataset = module.parse_dataset(dataset_payload, sha256="d" * 64)

    with _server(host_state) as host_url, _server(openshell_state) as openshell_url:
        raw, summary = module.evaluate(
            dataset,
            host_client=module.RunsClient(runs_url=host_url, api_key=HOST_KEY, clock=TickClock()),
            openshell_client=module.RunsClient(
                runs_url=openshell_url,
                api_key=OPENSHELL_KEY,
                clock=TickClock(),
            ),
            evaluation_id="synthetic-ab-001",
            prerequisites_path="var/openshell/eval/synthetic-ab-001/prerequisites.json",
            prerequisites_sha256="a" * 64,
            expected_primary_provider="synthetic",
            expected_primary_model="siq-eval-model",
        )

    assert [arm for arm, _payload in order[:4]] == ["host", "openshell", "openshell", "host"]
    assert len(order) == 60
    assert order[0][1] == order[1][1]
    assert order[2][1] == order[3][1]
    assert set(order[0][1]) == {
        "model",
        "temperature",
        "instructions",
        "input",
        "conversation_history",
        "session_id",
    }
    assert host_state.auth_failures == openshell_state.auth_failures == 0
    assert summary["quality_gate"] == {
        "passed": True,
        "failure_reasons": [],
        "cutover_performed": False,
        "recommendation": "manual_review_only_no_automatic_cutover",
    }
    for arm in ("host", "openshell"):
        metrics = summary["arms"][arm]
        assert metrics["task_success_rate"] == 1
        assert metrics["answer_citation_rate"] == 1
        assert metrics["numeric_accuracy"] == 1
        assert metrics["hallucination_block_rate"] == 1
        assert metrics["evidence_coverage"] == 1
        assert metrics["tool_success_rate"] == 1
        assert metrics["tool_error_rate"] == 0
        assert metrics["tool_retry_rate"] == 0
        assert metrics["tool_recovery_rate"] == 1
        assert metrics["tool_unrecovered_failure_rate"] == 0
        assert metrics["tool_runtime"] == {
            "attempt_count": 15,
            "success_count": 15,
            "failure_count": 0,
            "retry_count": 0,
            "failed_tool_state_count": 0,
            "recovered_tool_state_count": 0,
            "unrecovered_tool_state_count": 0,
        }
        assert metrics["fallback_success_rate"] == 1
        assert metrics["fallback_telemetry_coverage"] == 1
        assert metrics["report_completeness"] == 1
        assert metrics["timeout_rate"] == 0
        assert metrics["policy_false_positive_rate"] == 0
        assert metrics["latency_ms"]["ttft_p50"] is not None
        assert metrics["latency_ms"]["total_p95"] is not None

    project = tmp_path / "project"
    project.mkdir()
    raw_path, summary_path = module.write_evaluation_artifacts(
        project_root=project,
        evaluation_id="synthetic-ab-001",
        raw=raw,
        summary=summary,
    )
    assert raw_path == project / "var" / "openshell" / "eval" / "synthetic-ab-001" / "raw-results.json"
    assert summary_path == raw_path.with_name("summary.json")
    assert stat_mode(raw_path) == stat_mode(summary_path) == 0o600
    serialized = raw_path.read_text(encoding="utf-8") + summary_path.read_text(encoding="utf-8")
    for forbidden in (
        HOST_KEY,
        OPENSHELL_KEY,
        INSTRUCTIONS,
        CASE_ONE_INPUT,
        CASE_TWO_INPUT,
        CASE_ONE_OUTPUT,
        CASE_TWO_OUTPUT,
        host_url,
        openshell_url,
        "Authorization",
        '"input"',
    ):
        assert forbidden not in serialized
    assert not check_sanitized_artifacts.scan_paths([raw_path, summary_path])

    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir(mode=0o700)
    exported = export_sanitized_evidence.export_evidence([summary_path], output_root=evidence_root)
    assert len(exported) == 2
    assert not check_sanitized_artifacts.scan_paths(exported)


def stat_mode(path: Path) -> int:
    return path.stat().st_mode & 0o777


@pytest.mark.parametrize(
    ("behavior", "expected_status", "expected_error", "expected_stop"),
    [
        ("bad_create_status", "create_failed", "create_http_200", False),
        ("redirect_create", "create_failed", "http_post_302", False),
        ("bad_sse_content_type", "sse_failed", "sse_content_type", True),
        ("timeout", "timed_out", "run_timeout", True),
        ("bad_stop_contract", "timed_out", "run_timeout", True),
        ("heartbeat_then_stall", "timed_out", "run_timeout", True),
    ],
)
def test_create_sse_timeout_and_stop_contracts(
    behavior: str,
    expected_status: str,
    expected_error: str,
    expected_stop: bool,
) -> None:
    state = ServerState("host", HOST_KEY, behavior=behavior)
    payload = {
        "model": "siq-eval-model",
        "temperature": 0.1,
        "instructions": "synthetic",
        "input": CASE_ONE_INPUT,
        "conversation_history": [],
        "session_id": "synthetic-contract",
    }
    timeout = 0.1 if behavior == "heartbeat_then_stall" else 0.05 if behavior in {"timeout", "bad_stop_contract"} else 1
    with _server(state) as url:
        observed = module.RunsClient(runs_url=url, api_key=HOST_KEY).execute(
            payload,
            timeout_seconds=timeout,
        )

    assert observed.status == expected_status
    assert observed.error_code == expected_error
    if behavior == "heartbeat_then_stall":
        assert observed.total_duration_ms < 150
    assert observed.stop_attempted is expected_stop
    if expected_stop:
        assert len(state.stop_calls) == 1
        assert observed.stop_contract_ok is (behavior != "bad_stop_contract")
    else:
        assert state.stop_calls == []
        assert observed.stop_contract_ok is None


def test_failed_candidate_never_requests_or_reports_cutover() -> None:
    dataset = module.parse_dataset(_dataset_payload(), sha256="d" * 64)
    host_state = ServerState("host", HOST_KEY)
    openshell_state = ServerState("openshell", OPENSHELL_KEY, behavior="bad_sse_content_type")
    with _server(host_state) as host_url, _server(openshell_state) as openshell_url:
        raw, summary = module.evaluate(
            dataset,
            host_client=module.RunsClient(runs_url=host_url, api_key=HOST_KEY, clock=TickClock()),
            openshell_client=module.RunsClient(
                runs_url=openshell_url,
                api_key=OPENSHELL_KEY,
                clock=TickClock(),
            ),
            evaluation_id="synthetic-failed-candidate",
            prerequisites_path="var/openshell/eval/synthetic-failed-candidate/prerequisites.json",
            prerequisites_sha256="a" * 64,
            expected_primary_provider="synthetic",
            expected_primary_model="siq-eval-model",
        )

    assert summary["quality_gate"]["passed"] is False
    assert "openshell_contract_failure" in summary["quality_gate"]["failure_reasons"]
    assert summary["quality_gate"]["cutover_performed"] is False
    assert raw["cutover_performed"] is False
    assert summary["arms"]["host"]["runtime_telemetry"]["configured_route_match_count"] == 30


def test_missing_fallback_runtime_is_a_hard_gate_failure() -> None:
    dataset = module.parse_dataset(_dataset_payload(), sha256="d" * 64)
    host_state = ServerState("host", HOST_KEY)
    openshell_state = ServerState("openshell", OPENSHELL_KEY, behavior="missing_runtime")
    with _server(host_state) as host_url, _server(openshell_state) as openshell_url:
        _raw, summary = module.evaluate(
            dataset,
            host_client=module.RunsClient(runs_url=host_url, api_key=HOST_KEY, clock=TickClock()),
            openshell_client=module.RunsClient(
                runs_url=openshell_url,
                api_key=OPENSHELL_KEY,
                clock=TickClock(),
            ),
            evaluation_id="synthetic-missing-fallback-runtime",
            prerequisites_path="var/openshell/eval/synthetic-missing-fallback-runtime/prerequisites.json",
            prerequisites_sha256="a" * 64,
            expected_primary_provider="synthetic",
            expected_primary_model="siq-eval-model",
        )

    candidate = summary["arms"]["openshell"]
    assert candidate["fallback_telemetry_coverage"] == 0
    assert candidate["fallback_success_rate"] == 0
    assert summary["quality_gate"]["passed"] is False
    assert "openshell_fallback_telemetry_incomplete" in summary["quality_gate"]["failure_reasons"]
    assert "openshell_fallback_validation_failed" in summary["quality_gate"]["failure_reasons"]


def test_policy_false_positive_and_expected_denial_scoring() -> None:
    dataset = module.parse_dataset(_dataset_payload(), sha256="d" * 64)
    normal_case = dataset.cases[0]
    denied = module.RunObservation(
        status="failed",
        policy_denied=True,
        create_contract_ok=True,
        sse_contract_ok=True,
        terminal_contract_ok=True,
    )
    normal_score = module.score_run(normal_case, denied)
    assert normal_score["task_success"] is False
    assert normal_score["policy_false_positive"] is True

    expected_payload = _dataset_payload()
    expected_payload["cases"][0]["expectations"]["policy_denial_expected"] = True
    expected_case = module.parse_dataset(expected_payload, sha256="d" * 64).cases[0]
    expected_score = module.score_run(expected_case, denied)
    assert expected_score["task_success"] is True
    assert expected_score["policy_false_positive"] is False
    assert expected_score["policy_false_positive_eligible"] is False


def _quality_arm(*, execution_count: int = 30) -> dict[str, Any]:
    return {
        "execution_count": execution_count,
        "task_success_rate": 1.0,
        "answer_citation_rate": 1.0,
        "numeric_accuracy": 1.0,
        "hallucination_block_rate": 1.0,
        "evidence_coverage": 1.0,
        "tool_success_rate": 1.0,
        "tool_error_rate": 0.0,
        "tool_retry_rate": 0.0,
        "tool_recovery_rate": 1.0,
        "tool_unrecovered_failure_rate": 0.0,
        "fallback_success_rate": 1.0,
        "fallback_telemetry_coverage": 1.0,
        "fallback_expected_execution_count": 3,
        "fallback_telemetry_expected_count": 3,
        "report_completeness": 1.0,
        "timeout_rate": 0.0,
        "policy_false_positive_rate": 0.0,
        "sample_counts": {
            "answer_citation_rate": execution_count,
            "numeric_accuracy": execution_count,
            "hallucination_block_rate": execution_count,
            "evidence_coverage": execution_count,
            "tool_success_rate": execution_count,
            "report_completeness": execution_count,
            "policy_false_positive_rate": execution_count,
        },
        "contract_failure_count": 0,
        "unexpected_fallback_count": 0,
        "tool_runtime": {
            "attempt_count": execution_count,
            "success_count": execution_count,
            "failure_count": 0,
            "retry_count": 0,
            "failed_tool_state_count": 0,
            "recovered_tool_state_count": 0,
            "unrecovered_tool_state_count": 0,
        },
        "runtime_telemetry": {
            "expected_primary_provider": "synthetic",
            "expected_primary_model": "siq-eval-model",
            "telemetry_count": execution_count,
            "requested_model_match_count": execution_count,
            "configured_route_match_count": execution_count,
            "effective_route_match_count": execution_count,
            "fallback_inactive_count": execution_count,
            "configured_routes": [
                {"provider": "synthetic", "model": "siq-eval-model", "count": execution_count}
            ],
            "effective_routes": [
                {"provider": "synthetic", "model": "siq-eval-model", "count": execution_count}
            ],
        },
        "latency_ms": {
            "ttft_sample_count": execution_count,
            "ttft_p50": 10.0,
            "ttft_p95": 20.0,
            "total_sample_count": execution_count,
            "total_p50": 100.0,
            "total_p95": 100.0,
        },
    }


def test_quality_gate_uses_exact_task_p95_and_golden_false_positive_thresholds() -> None:
    host = _quality_arm()
    candidate = _quality_arm()

    candidate["task_success_rate"] = 0.99
    _comparison, reasons = module.quality_comparison(host, candidate, case_count=10, repetitions=3)
    assert "task_success_regression" in reasons

    candidate = _quality_arm()
    candidate["latency_ms"]["total_p95"] = 110.001
    _comparison, reasons = module.quality_comparison(host, candidate, case_count=10, repetitions=3)
    assert "total_p95_regression" in reasons

    candidate = _quality_arm()
    candidate["policy_false_positive_rate"] = 0.001
    _comparison, reasons = module.quality_comparison(host, candidate, case_count=10, repetitions=3)
    assert "golden_policy_false_positive" in reasons


def test_quality_gate_rejects_insufficient_samples_as_uncertain() -> None:
    host = _quality_arm(execution_count=2)
    candidate = _quality_arm(execution_count=2)
    for arm in (host, candidate):
        arm["fallback_expected_execution_count"] = 1
        arm["fallback_telemetry_expected_count"] = 1
        arm["latency_ms"]["ttft_sample_count"] = 2
        arm["latency_ms"]["total_sample_count"] = 2
        for key in arm["sample_counts"]:
            arm["sample_counts"][key] = 2

    _comparison, reasons = module.quality_comparison(host, candidate, case_count=2, repetitions=1)

    assert "evaluation_case_count_insufficient" in reasons
    assert "evaluation_repetitions_insufficient" in reasons
    assert "host_execution_sample_insufficient" in reasons
    assert "openshell_execution_sample_insufficient" in reasons
    assert "host_primary_metric_sample_insufficient" in reasons
    assert "openshell_primary_metric_sample_insufficient" in reasons
    assert "host_latency_sample_insufficient" in reasons
    assert "openshell_latency_sample_insufficient" in reasons


def test_release_sized_equal_arms_have_no_statistical_gate_failure() -> None:
    host = _quality_arm()
    candidate = _quality_arm()

    _comparison, reasons = module.quality_comparison(host, candidate, case_count=10, repetitions=3)

    assert reasons == []


def test_only_openshell_candidate_is_subject_to_absolute_quality_limits() -> None:
    host = _quality_arm()
    candidate = _quality_arm()
    for metric in module.OPENSHELL_ABSOLUTE_QUALITY_FLOORS:
        host[metric] = 0.0
    host["timeout_rate"] = 1.0
    host["policy_false_positive_rate"] = 1.0

    _comparison, reasons = module.quality_comparison(host, candidate, case_count=10, repetitions=3)

    assert reasons == []

    for metric in module.OPENSHELL_ABSOLUTE_QUALITY_FLOORS:
        candidate[metric] = 0.0
    candidate["timeout_rate"] = 1.0
    candidate["policy_false_positive_rate"] = 1.0

    _comparison, reasons = module.quality_comparison(host, candidate, case_count=10, repetitions=3)

    assert "openshell_task_success_rate_below_absolute_floor" in reasons
    assert "openshell_report_completeness_below_absolute_floor" in reasons
    assert not any(reason.startswith("host_") and "absolute" in reason for reason in reasons)
    assert "openshell_policy_false_positive_rate_above_absolute_ceiling" in reasons
    assert "openshell_timeout_rate_above_absolute_ceiling" in reasons


def test_tool_success_has_only_a_comparative_no_regression_gate() -> None:
    host = _quality_arm()
    candidate = _quality_arm()
    host["tool_success_rate"] = candidate["tool_success_rate"] = 0.0

    _comparison, reasons = module.quality_comparison(host, candidate, case_count=10, repetitions=3)

    assert reasons == []

    candidate["tool_error_rate"] = 0.5
    candidate["tool_retry_rate"] = 0.5
    candidate["tool_recovery_rate"] = 0.5
    candidate["tool_unrecovered_failure_rate"] = 0.5
    comparison, reasons = module.quality_comparison(host, candidate, case_count=10, repetitions=3)

    assert reasons == []
    assert comparison["metric_deltas"]["tool_error_rate"] == 0.5
    assert comparison["metric_deltas"]["tool_retry_rate"] == 0.5

    host["tool_success_rate"] = 1.0
    _comparison, reasons = module.quality_comparison(host, candidate, case_count=10, repetitions=3)

    assert "tool_success_rate_regression" in reasons
    assert not any("tool_success_rate_below_absolute_floor" in reason for reason in reasons)


def test_normal_primary_route_telemetry_is_a_hard_gate() -> None:
    host = _quality_arm()
    candidate = _quality_arm()
    for arm in (host, candidate):
        arm["fallback_success_rate"] = None
        arm["fallback_telemetry_coverage"] = None
        arm["fallback_expected_execution_count"] = 0
        arm["fallback_telemetry_expected_count"] = 0
    candidate["runtime_telemetry"]["effective_route_match_count"] = 29
    candidate["runtime_telemetry"]["effective_routes"] = [
        {"provider": "synthetic", "model": "siq-eval-model", "count": 29},
        {"provider": "unexpected", "model": "unexpected", "count": 1},
    ]

    _comparison, reasons = module.quality_comparison(
        host,
        candidate,
        case_count=10,
        repetitions=3,
        require_fallback=False,
    )

    assert "openshell_primary_route_not_effective" in reasons
    assert "runtime_route_distribution_mismatch" in reasons


def test_normal_only_release_suite_does_not_require_embedded_fallback_samples() -> None:
    host = _quality_arm()
    candidate = _quality_arm()
    for arm in (host, candidate):
        arm["fallback_success_rate"] = None
        arm["fallback_telemetry_coverage"] = None
        arm["fallback_expected_execution_count"] = 0
        arm["fallback_telemetry_expected_count"] = 0

    _comparison, reasons = module.quality_comparison(
        host,
        candidate,
        case_count=10,
        repetitions=3,
        require_fallback=False,
    )

    assert reasons == []


@pytest.mark.parametrize(
    (
        "behavior",
        "expected_successful",
        "expected_tool_score",
        "expected_recovery_rate",
        "expected_unrecovered_rate",
    ),
    [
        ("tool_failure_then_success", {"pg_query"}, {"matched": 1, "expected": 1}, 1.0, 0.0),
        ("tool_success_then_failure", set(), {"matched": 0, "expected": 1}, 0.0, 1.0),
    ],
)
def test_required_tool_scoring_uses_final_completion_state(
    behavior: str,
    expected_successful: set[str],
    expected_tool_score: dict[str, int],
    expected_recovery_rate: float,
    expected_unrecovered_rate: float,
) -> None:
    case = module.parse_dataset(_dataset_payload(), sha256="d" * 64).cases[0]
    state = ServerState("host", HOST_KEY, behavior=behavior)
    with _server(state) as url:
        observation = module.RunsClient(runs_url=url, api_key=HOST_KEY).execute(
            {
                "model": "siq-eval-model",
                "temperature": 0.1,
                "instructions": "synthetic",
                "input": CASE_ONE_INPUT,
                "conversation_history": [],
                "session_id": "tool-final-state",
            },
            timeout_seconds=1,
        )

    score = module.score_run(case, observation)
    record = module._observation_record(
        sequence=1,
        arm="host",
        repetition=0,
        case=case,
        payload_sha256="a" * 64,
        observation=observation,
        scores=score,
    )
    metrics = module.summarize_arm(
        [record],
        expected_primary_provider="synthetic",
        expected_primary_model="siq-eval-model",
    )

    assert observation.failed_tools == {"pg_query"}
    assert observation.successful_tools == expected_successful
    assert observation.tool_attempt_counts == {"pg_query": 2}
    assert observation.tool_success_counts == {"pg_query": 1}
    assert observation.tool_failure_counts == {"pg_query": 1}
    assert score["tools"] == expected_tool_score
    assert score["task_success"] is True
    assert record["tool_outcomes"] == {
        "pg_query": {
            "attempts": 2,
            "successes": 1,
            "failures": 1,
            "final_status": "success" if expected_successful else "failure",
        }
    }
    assert metrics["tool_error_rate"] == 0.5
    assert metrics["tool_retry_rate"] == 0.5
    assert metrics["tool_recovery_rate"] == expected_recovery_rate
    assert metrics["tool_unrecovered_failure_rate"] == expected_unrecovered_rate


def test_dataset_url_key_and_cli_security_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = json.loads(
        (Path(__file__).resolve().parents[3] / "infra/openshell/schemas/siq-analysis-ab-dataset.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["schema_version"]["const"] == module.DATASET_SCHEMA_VERSION
    module.parse_dataset(_dataset_payload(), sha256="d" * 64)

    duplicate_json = tmp_path / "duplicate.json"
    duplicate_json.write_text(
        '{"schema_version":"first","schema_version":"second"}',
        encoding="utf-8",
    )
    with pytest.raises(module.EvaluationConfigurationError, match="dataset_json_invalid"):
        module.load_dataset(duplicate_json)

    invalid = _dataset_payload()
    invalid["unexpected"] = True
    with pytest.raises(module.EvaluationConfigurationError, match="dataset_schema_invalid"):
        module.parse_dataset(invalid, sha256="d" * 64)

    duplicate = _dataset_payload()
    duplicate["cases"][1]["case_id"] = duplicate["cases"][0]["case_id"]
    with pytest.raises(module.EvaluationConfigurationError, match="dataset_case_id_duplicate"):
        module.parse_dataset(duplicate, sha256="d" * 64)

    assert module.normalize_runs_url("http://127.0.0.1:18642/v1/runs/") == "http://127.0.0.1:18642/v1/runs"
    for invalid_url in (
        "https://127.0.0.1:18642/v1/runs",
        "http://example.com:18642/v1/runs",
        "http://127.0.0.1/v1/runs",
        "http://127.0.0.1:18642/health",
    ):
        with pytest.raises(module.EvaluationConfigurationError):
            module.normalize_runs_url(invalid_url)

    key_file = _write_key(tmp_path / "key", HOST_KEY, 0o644)
    with pytest.raises(module.EvaluationConfigurationError, match="permissions"):
        module.load_api_key(key_file)
    key_file.chmod(0o600)
    assert module.load_api_key(key_file) == HOST_KEY

    option_strings = {option for action in module._parser()._actions for option in action.option_strings}
    assert "--host-api-key" not in option_strings
    assert "--openshell-api-key" not in option_strings
    assert "--host-api-key-file" in option_strings
    assert "--openshell-api-key-file" in option_strings
    assert "--prerequisites" in option_strings
    assert next(action for action in module._parser()._actions if action.dest == "prerequisites").required is True

    client = module.RunsClient(runs_url="http://127.0.0.1:18642/v1/runs", api_key=HOST_KEY)
    opener = client._opener.__self__
    proxy_handlers = [handler for handler in opener.handlers if isinstance(handler, module.urllib.request.ProxyHandler)]
    assert proxy_handlers == []
    assert any(isinstance(handler, module.NoRedirectHandler) for handler in opener.handlers)

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    state = ServerState("host", HOST_KEY)
    with _server(state) as url:
        observed = module.RunsClient(runs_url=url, api_key=HOST_KEY).execute(
            {
                "model": "siq-eval-model",
                "temperature": 0.1,
                "instructions": "synthetic",
                "input": CASE_ONE_INPUT,
                "conversation_history": [],
                "session_id": "proxy-bypass-check",
            },
            timeout_seconds=1,
        )
    assert observed.status == "completed"


def test_cli_requires_confirmation_and_separate_endpoints_and_key_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(_dataset_payload()), encoding="utf-8")
    key_path = _write_key(tmp_path / "key", HOST_KEY)
    prerequisites_path = tmp_path / "var/openshell/eval/synthetic-cli/prerequisites.json"
    common = [
        "--dataset",
        str(dataset_path),
        "--host-runs-url",
        "http://127.0.0.1:19001/v1/runs",
        "--openshell-runs-url",
        "http://127.0.0.1:19001/v1/runs",
        "--host-api-key-file",
        str(key_path),
        "--openshell-api-key-file",
        str(key_path),
        "--evaluation-id",
        "synthetic-cli",
        "--project-root",
        str(tmp_path),
        "--prerequisites",
        str(prerequisites_path),
    ]
    assert module.main(common) == 2
    assert "live_evaluation_not_confirmed" in capsys.readouterr().err
    assert module.main([*common, "--confirm-live-evaluation"]) == 2
    captured = capsys.readouterr()
    assert "ab_endpoints_must_differ" in captured.err
    assert HOST_KEY not in captured.err + captured.out
    assert not (tmp_path / "var").exists()


def test_full_cli_uses_distinct_key_files_and_writes_only_eval_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(_dataset_payload()), encoding="utf-8")
    host_key_path = _write_key(tmp_path / "host.key", HOST_KEY)
    openshell_key_path = _write_key(tmp_path / "openshell.key", OPENSHELL_KEY)
    prerequisites_path = tmp_path / "var/openshell/eval/synthetic-cli-full/prerequisites.json"
    host_state = ServerState("host", HOST_KEY)
    openshell_state = ServerState("openshell", OPENSHELL_KEY)
    real_client = module.RunsClient
    observed_prerequisites: dict[str, Any] = {}

    def deterministic_client(*, runs_url: str, api_key: str) -> module.RunsClient:
        return real_client(runs_url=runs_url, api_key=api_key, clock=TickClock())

    def prerequisite_binding(_path: Path, **kwargs: Any) -> tuple[str, str, str, str]:
        observed_prerequisites.update(kwargs)
        return (
            "var/openshell/eval/synthetic-cli-full/prerequisites.json",
            "a" * 64,
            "synthetic",
            "siq-eval-model",
        )

    monkeypatch.setattr(module, "RunsClient", deterministic_client)
    monkeypatch.setattr(module, "_load_prerequisite_binding", prerequisite_binding)
    with _server(host_state) as host_url, _server(openshell_state) as openshell_url:
        result = module.main(
            [
                "--dataset",
                str(dataset_path),
                "--host-runs-url",
                host_url,
                "--openshell-runs-url",
                openshell_url,
                "--host-api-key-file",
                str(host_key_path),
                "--openshell-api-key-file",
                str(openshell_key_path),
                "--evaluation-id",
                "synthetic-cli-full",
                "--project-root",
                str(tmp_path),
                "--prerequisites",
                str(prerequisites_path),
                "--confirm-live-evaluation",
            ]
        )

    assert result == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out) == {
        "ok": True,
        "schema_version": module.SUMMARY_SCHEMA_VERSION,
        "evaluation_id": "synthetic-cli-full",
        "cutover_performed": False,
    }
    assert captured.err == ""
    assert observed_prerequisites == {
        "project_root": tmp_path,
        "evaluation_id": "synthetic-cli-full",
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "host_runs_url": host_url,
        "openshell_runs_url": openshell_url,
        "host_key_fingerprint": hashlib.sha256(HOST_KEY.encode()).hexdigest(),
        "openshell_key_fingerprint": hashlib.sha256(OPENSHELL_KEY.encode()).hexdigest(),
    }
    output = tmp_path / "var" / "openshell" / "eval" / "synthetic-cli-full"
    assert sorted(path.name for path in output.iterdir()) == ["raw-results.json", "summary.json"]
    assert all(stat_mode(path) == 0o600 for path in output.iterdir())
    assert json.loads((output / "raw-results.json").read_text(encoding="utf-8"))["prerequisites_path"] == (
        "var/openshell/eval/synthetic-cli-full/prerequisites.json"
    )
    assert json.loads((output / "raw-results.json").read_text(encoding="utf-8"))["prerequisites_sha256"] == "a" * 64
    summary_payload = json.loads((output / "summary.json").read_text(encoding="utf-8"))
    assert summary_payload["prerequisites_path"] == (
        "var/openshell/eval/synthetic-cli-full/prerequisites.json"
    )
    assert summary_payload["prerequisites_sha256"] == "a" * 64


def test_prerequisite_failure_stops_before_any_runs_client_or_network_use(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(_dataset_payload()), encoding="utf-8")
    host_key_path = _write_key(tmp_path / "host.key", HOST_KEY)
    openshell_key_path = _write_key(tmp_path / "openshell.key", OPENSHELL_KEY)

    def reject_prerequisites(*_args: Any, **_kwargs: Any) -> str:
        raise module.EvaluationConfigurationError("prerequisites_dataset_drift")

    def unexpected_client(**_kwargs: Any) -> module.RunsClient:
        raise AssertionError("RunsClient must not be constructed before prerequisite validation")

    monkeypatch.setattr(module, "_load_prerequisite_binding", reject_prerequisites)
    monkeypatch.setattr(module, "RunsClient", unexpected_client)
    result = module.main(
        [
            "--dataset",
            str(dataset_path),
            "--host-runs-url",
            "http://127.0.0.1:18651/v1/runs",
            "--openshell-runs-url",
            "http://127.0.0.1:28651/v1/runs",
            "--host-api-key-file",
            str(host_key_path),
            "--openshell-api-key-file",
            str(openshell_key_path),
            "--evaluation-id",
            "eval-20260716-a",
            "--project-root",
            str(tmp_path),
            "--prerequisites",
            str(tmp_path / "prerequisites.json"),
            "--confirm-live-evaluation",
        ]
    )

    assert result == 2
    assert "prerequisites_dataset_drift" in capsys.readouterr().err
    assert not (tmp_path / "var").exists()


def test_output_directory_preflight_stops_before_constructing_runs_client(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(_dataset_payload()), encoding="utf-8")
    host_key_path = _write_key(tmp_path / "host.key", HOST_KEY)
    openshell_key_path = _write_key(tmp_path / "openshell.key", OPENSHELL_KEY)
    evaluation_id = "eval-output-preflight"
    output = tmp_path / "var/openshell/eval" / evaluation_id
    output.mkdir(parents=True, mode=0o700)
    prerequisites_path = output / "prerequisites.json"
    prerequisites_path.write_text("{}\n", encoding="utf-8")
    prerequisites_path.chmod(0o600)
    unexpected = output / "free-form.log"
    unexpected.write_text("not approved\n", encoding="utf-8")
    unexpected.chmod(0o600)

    monkeypatch.setattr(
        module,
        "_load_prerequisite_binding",
        lambda *_args, **_kwargs: (
            f"var/openshell/eval/{evaluation_id}/prerequisites.json",
            "a" * 64,
            "synthetic",
            "siq-eval-model",
        ),
    )

    def unexpected_client(**_kwargs: Any) -> module.RunsClient:
        raise AssertionError("RunsClient must not be constructed before output preflight")

    monkeypatch.setattr(module, "RunsClient", unexpected_client)
    result = module.main(
        [
            "--dataset",
            str(dataset_path),
            "--host-runs-url",
            "http://127.0.0.1:18651/v1/runs",
            "--openshell-runs-url",
            "http://127.0.0.1:28651/v1/runs",
            "--host-api-key-file",
            str(host_key_path),
            "--openshell-api-key-file",
            str(openshell_key_path),
            "--evaluation-id",
            evaluation_id,
            "--project-root",
            str(tmp_path),
            "--prerequisites",
            str(prerequisites_path),
            "--confirm-live-evaluation",
        ]
    )

    assert result == 2
    assert "evaluation_output_contains_unexpected_entry" in capsys.readouterr().err
    assert not (output / "raw-results.json").exists()
    assert not (output / "summary.json").exists()


def test_cli_rejects_equal_key_material_even_in_distinct_files(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dataset_path = tmp_path / "dataset.json"
    dataset_path.write_text(json.dumps(_dataset_payload()), encoding="utf-8")
    host_key_path = _write_key(tmp_path / "host.key", HOST_KEY)
    openshell_key_path = _write_key(tmp_path / "openshell.key", HOST_KEY)
    prerequisites_path = tmp_path / "var/openshell/eval/synthetic-cli-equal-keys/prerequisites.json"
    result = module.main(
        [
            "--dataset",
            str(dataset_path),
            "--host-runs-url",
            "http://127.0.0.1:19001/v1/runs",
            "--openshell-runs-url",
            "http://127.0.0.1:19002/v1/runs",
            "--host-api-key-file",
            str(host_key_path),
            "--openshell-api-key-file",
            str(openshell_key_path),
            "--evaluation-id",
            "synthetic-cli-equal-keys",
            "--project-root",
            str(tmp_path),
            "--prerequisites",
            str(prerequisites_path),
            "--confirm-live-evaluation",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert "api_keys_must_differ" in captured.err
    assert HOST_KEY not in captured.err + captured.out
    assert not (tmp_path / "var").exists()


def test_artifact_writer_rejects_path_traversal_evaluation_id(tmp_path: Path) -> None:
    with pytest.raises(module.EvaluationConfigurationError, match="evaluation_id_invalid"):
        module.write_evaluation_artifacts(
            project_root=tmp_path,
            evaluation_id="../outside",
            raw={"schema_version": module.RAW_SCHEMA_VERSION},
            summary={"schema_version": module.SUMMARY_SCHEMA_VERSION},
        )
    assert not (tmp_path.parent / "outside").exists()


def test_artifact_writer_does_not_change_existing_parent_directory_modes(tmp_path: Path) -> None:
    var = tmp_path / "var"
    openshell = var / "openshell"
    openshell.mkdir(parents=True)
    var.chmod(0o755)
    openshell.chmod(0o750)

    raw_path, _summary_path = module.write_evaluation_artifacts(
        project_root=tmp_path,
        evaluation_id="parent-mode-check",
        raw={"schema_version": module.RAW_SCHEMA_VERSION},
        summary={"schema_version": module.SUMMARY_SCHEMA_VERSION},
    )

    assert stat_mode(var) == 0o755
    assert stat_mode(openshell) == 0o750
    assert stat_mode(raw_path.parent.parent) == 0o700
    assert stat_mode(raw_path.parent) == 0o700


def test_artifact_writer_accepts_only_strict_preexisting_preparation_files(tmp_path: Path) -> None:
    output = tmp_path / "var/openshell/eval/live-prepared"
    output.mkdir(parents=True, mode=0o700)
    prepared_names = {
        "dataset.json",
        "source-bindings.json",
        "host.key",
        "host-key-receipt.json",
        "host-runtime-receipt.json",
        "provenance.json",
        "prerequisites.json",
    }
    for name in prepared_names:
        prepared = output / name
        prepared.write_text("{}\n", encoding="utf-8")
        prepared.chmod(0o600)

    raw_path, summary_path = module.write_evaluation_artifacts(
        project_root=tmp_path,
        evaluation_id="live-prepared",
        raw={"schema_version": module.RAW_SCHEMA_VERSION},
        summary={"schema_version": module.SUMMARY_SCHEMA_VERSION},
    )

    assert all((output / name).exists() for name in prepared_names)
    assert raw_path.exists()
    assert summary_path.exists()


@pytest.mark.parametrize("entry_kind", ["unexpected", "symlink"])
def test_artifact_writer_rejects_unexpected_or_symlinked_preparation_entry(
    tmp_path: Path,
    entry_kind: str,
) -> None:
    output = tmp_path / "var/openshell/eval/live-prepared"
    output.mkdir(parents=True, mode=0o700)
    if entry_kind == "unexpected":
        entry = output / "free-form.log"
        entry.write_text("not approved\n", encoding="utf-8")
    else:
        target = tmp_path / "target"
        target.write_text("{}\n", encoding="utf-8")
        entry = output / "prerequisites.json"
        entry.symlink_to(target)

    with pytest.raises(module.EvaluationConfigurationError):
        module.write_evaluation_artifacts(
            project_root=tmp_path,
            evaluation_id="live-prepared",
            raw={"schema_version": module.RAW_SCHEMA_VERSION},
            summary={"schema_version": module.SUMMARY_SCHEMA_VERSION},
        )

    assert not (output / "raw-results.json").exists()
    assert not (output / "summary.json").exists()
