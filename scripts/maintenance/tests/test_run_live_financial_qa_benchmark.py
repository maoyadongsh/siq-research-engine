from __future__ import annotations

import importlib.util
import io
import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SOURCE = REPO_ROOT / "scripts" / "maintenance" / "run_live_financial_qa_benchmark.py"
spec = importlib.util.spec_from_file_location("live_financial_qa_benchmark_under_test", SOURCE)
assert spec and spec.loader
live = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = live
spec.loader.exec_module(live)

DETERMINISTIC = importlib.util.spec_from_file_location(
    "deterministic_financial_qa_for_live_tests", REPO_ROOT / "scripts" / "maintenance" / "run_financial_qa_benchmark.py"
)
assert DETERMINISTIC and DETERMINISTIC.loader
deterministic = importlib.util.module_from_spec(DETERMINISTIC)
sys.modules[DETERMINISTIC.name] = deterministic
DETERMINISTIC.loader.exec_module(deterministic)


def _trace_map() -> dict[str, dict]:
    return deterministic.load_trace_map(REPO_ROOT / "datasets/eval/financial_qa_benchmark/v1/traces/p0_golden_traces.jsonl")


def _case_root(tmp_path: Path, case_ids: list[str]) -> Path:
    cases = [case for case in deterministic.load_cases(REPO_ROOT / "datasets/eval/financial_qa_benchmark/v1") if case["case_id"] in case_ids]
    root = tmp_path / "cases"
    root.mkdir(exist_ok=True)
    (root / "cases.jsonl").write_text("\n".join(json.dumps(case, ensure_ascii=False) for case in cases) + "\n", encoding="utf-8")
    return root


class FakeTransport:
    def __init__(self, payloads: dict[str, dict], *, sse: bool = False):
        self.payloads = payloads
        self.sse = sse
        self.requests: list[dict] = []

    def request(self, *, url: str, body: bytes, headers: dict[str, str], timeout: float):
        request = json.loads(body)
        self.requests.append(request)
        payload = self.payloads[request["case_id"]]
        if self.sse:
            trace = payload["answer_audit_trace"]
            data = (
                'data: {"delta":"sanitized answer"}\n\n'
                + "data: "
                + json.dumps({"done": True, "answer_audit_trace": trace, "model": "fake-live", "usage": {"total_tokens": 7}})
                + "\n\n"
            )
            return live.TransportResponse(200, {"content-type": "text/event-stream"}, io.BytesIO(data.encode()))
        return live.TransportResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode())


class FakeHermesTransport:
    def __init__(self, payload: dict, *, run_id: str = "run_financial_qa_1", event: str = "run.completed"):
        self.payload = payload
        self.run_id = run_id
        self.event = event
        self.requests: list[dict] = []

    def request(self, *, url: str, body: bytes, headers: dict[str, str], timeout: float):
        self.requests.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        if body:
            return live.TransportResponse(
                200,
                {"content-type": "application/json"},
                json.dumps({"run_id": self.run_id, "status": "started"}).encode(),
            )
        event_payload = {
            "event": self.event,
            "output": json.dumps(self.payload, ensure_ascii=False),
            "model": "hermes-financial",
            "usage": {"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
        }
        data = f"data: {json.dumps(event_payload, ensure_ascii=False)}\n\n".encode()
        return live.TransportResponse(200, {"content-type": "text/event-stream"}, io.BytesIO(data))


class FakeSIQChatTransport:
    def __init__(self, case_id: str, *, trace: dict | None = None):
        self.case_id = case_id
        self.trace = trace if trace is not None else _trace_map()[case_id]
        self.requests: list[dict] = []

    def request(self, *, url: str, body: bytes, headers: dict[str, str], timeout: float):
        self.requests.append({"url": url, "body": body, "headers": headers, "timeout": timeout})
        if body:
            request = json.loads(body)
            assert request["message"].startswith(f"question_id={self.case_id}\n")
            payload = {
                "reply": "private runtime answer",
                "new_achievements": [],
                "audit_trace_id": "aat_" + "a" * 32,
            }
            return live.TransportResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode())
        payload = {"trace_id": "aat_" + "a" * 32, "trace": self.trace}
        return live.TransportResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode())


def _payload(case_id: str) -> dict:
    return {"answer_audit_trace": _trace_map()[case_id], "model": "fake-live", "usage": {"total_tokens": 7, "cost": "0.01"}}


def test_disabled_mode_never_calls_transport(tmp_path):
    class FailTransport:
        def request(self, **kwargs):
            raise AssertionError("disabled mode must not call external transport")

    report = live.run_live_benchmark(config=live.LiveModelConfig(), case_root=tmp_path, transport=FailTransport())
    assert report["status"] == "not_run"
    assert report["passed"] is False
    assert report["live_execution_completed"] is False


def test_required_disabled_mode_is_blocked_without_transport(tmp_path):
    class FailTransport:
        def request(self, **kwargs):
            raise AssertionError("required disabled mode must fail before transport")

    with pytest.raises(live.LiveModelError, match="required_live_benchmark_not_run"):
        live.run_live_benchmark(
            config=live.LiveModelConfig(required=True),
            case_root=tmp_path,
            transport=FailTransport(),
        )


def test_live_json_success_reuses_deterministic_verifier(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    transport = FakeTransport({case_id: _payload(case_id)})
    report = live.run_live_benchmark(
        config=live.LiveModelConfig(mode="live-http", endpoint="http://fake.test/v1/chat", protocol="json"),
        case_root=_case_root(tmp_path, [case_id]),
        transport=transport,
    )
    assert report["passed"] is True
    assert report["results"][0]["model"] == "fake-live"
    assert report["results"][0]["usage"]["total_tokens"] == 7
    assert report["results"][0]["answer_sha256"]
    assert "question" in transport.requests[0]
    assert "response_format" in transport.requests[0]
    assert report["live_execution_completed"] is True
    assert report["execution"]["network_requests_started"] == 1
    assert live.required_live_execution_satisfied(report) is True


def test_required_live_run_requires_https_and_auth_token(tmp_path):
    with pytest.raises(live.LiveModelError, match="required_live_benchmark_requires_https"):
        live.run_live_benchmark(
            config=live.LiveModelConfig(
                mode="live-http",
                endpoint="http://fake.test/v1/chat",
                required=True,
                auth_token="secret",
            ),
            case_root=tmp_path,
        )

    with pytest.raises(live.LiveModelError, match="required_live_benchmark_missing_auth_token"):
        live.run_live_benchmark(
            config=live.LiveModelConfig(mode="live-http", endpoint="https://fake.test/v1/chat", required=True),
            case_root=tmp_path,
        )


def test_redirect_handler_preserves_auth_only_within_same_https_origin():
    handler = live.SameOriginRedirectHandler()
    request = live.urllib.request.Request(
        "https://api.example.test/v1/chat",
        headers={"Authorization": "Bearer secret"},
    )

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "https://api.example.test/v1/chat/",
    )
    assert redirected.get_header("Authorization") == "Bearer secret"

    with pytest.raises(live.LiveModelError, match="unsafe_redirect"):
        handler.redirect_request(request, None, 302, "Found", {}, "https://other.example.test/collect")
    with pytest.raises(live.LiveModelError, match="unsafe_redirect"):
        handler.redirect_request(request, None, 302, "Found", {}, "http://api.example.test/collect")


@pytest.mark.parametrize(
    "diagnostic",
    [
        "Authorization: Bearer supersecret",
        "authorization='Bearer supersecret'",
        '{"authorization": "Bearer supersecret"}',
        "upstream rejected Bearer supersecret",
        "access_token=supersecret",
        '{"source_token": "supersecret"}',
    ],
)
def test_redact_text_removes_complete_auth_credentials(diagnostic):
    redacted = live.redact_text(diagnostic)

    assert "supersecret" not in redacted
    assert "[redacted]" in redacted


def test_required_live_run_records_non_empty_execution_proof(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    report = live.run_live_benchmark(
        config=live.LiveModelConfig(
            mode="live-http",
            endpoint="https://fake.test/v1/chat",
            protocol="json",
            auth_token="fake-test-token",
            required=True,
        ),
        case_root=_case_root(tmp_path, [case_id]),
        transport=FakeTransport({case_id: _payload(case_id)}),
    )

    assert report["passed"] is True
    assert report["summary"]["cases"] == 1
    assert report["execution"] == {
        "mode": "live-http",
        "case_attempts": 1,
        "network_requests_started": 1,
    }
    assert live.required_live_execution_satisfied(report) is True


def test_live_json_latency_includes_transport_and_model_wait(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"

    class DelayedTransport(FakeTransport):
        def request(self, **kwargs):
            time.sleep(0.01)
            return super().request(**kwargs)

    report = live.run_live_benchmark(
        config=live.LiveModelConfig(mode="live-http", endpoint="http://fake.test/v1/chat", protocol="json"),
        case_root=_case_root(tmp_path, [case_id]),
        transport=DelayedTransport({case_id: _payload(case_id)}),
    )

    item = report["results"][0]
    assert item["ttft_seconds"] >= 0.009
    assert item["total_latency_seconds"] >= 0.009


def test_live_sse_natural_refusal_is_verified_without_recording_answer(tmp_path):
    case_id = "p0-eu-asml-undisclosed-monthly-revenue-refusal"
    transport = FakeTransport({case_id: _payload(case_id)}, sse=True)
    report = live.run_live_benchmark(
        config=live.LiveModelConfig(mode="live-http", endpoint="https://fake.test/chat", protocol="sse"),
        case_root=_case_root(tmp_path, [case_id]),
        transport=transport,
    )
    assert report["passed"] is True
    item = report["results"][0]
    assert item["verification"]["guardrail_blocked"] is True
    assert item["answer_length"] == 16
    assert "sanitized answer" not in json.dumps(report, ensure_ascii=False)


def test_hermes_runs_creates_run_then_collects_events_without_recording_answer(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    payload = {"answer": "private financial answer", "answer_audit_trace": _trace_map()[case_id]}
    transport = FakeHermesTransport(payload)

    report = live.run_live_benchmark(
        config=live.LiveModelConfig(
            mode="live-http",
            endpoint="https://hermes.test/v1/runs",
            protocol="hermes-runs",
            model="siq_assistant",
            auth_token="temporary-secret",
        ),
        case_root=_case_root(tmp_path, [case_id]),
        transport=transport,
    )

    assert report["passed"] is True
    assert len(transport.requests) == 2
    create_request = transport.requests[0]
    create_payload = json.loads(create_request["body"])
    assert create_request["url"] == "https://hermes.test/v1/runs"
    assert create_payload["model"] == "siq_assistant"
    assert create_payload["input"].startswith("腾讯 2025 年营业收入是多少？")
    assert '"market": "HK"' in create_payload["input"]
    assert '"company_id": "HK:00700"' in create_payload["input"]
    assert '"filing_id":' in create_payload["input"]
    assert "do not infer or replace fields" in create_payload["input"]
    assert '"schema_version": "siq_answer_audit_trace_v1"' in create_payload["input"]
    assert '"resolved_company"' in create_payload["input"]
    assert '"resolved_period"' in create_payload["input"]
    assert '"wiki_facts"' in create_payload["input"]
    assert '"postgres_facts"' in create_payload["input"]
    assert '"calculator_runs"' in create_payload["input"]
    assert '"guardrail_result"' in create_payload["input"]
    assert "Never use semantic/vector retrieval as the numeric source" in create_payload["input"]
    assert "set guardrail_result.blocked=true" in create_payload["input"]
    assert "751766" not in create_payload["input"]
    assert create_payload["session_id"] == f"siq-financial-qa-{case_id}"
    assert transport.requests[1]["url"] == "https://hermes.test/v1/runs/run_financial_qa_1/events"
    assert transport.requests[1]["body"] == b""
    item = report["results"][0]
    assert item["model"] == "hermes-financial"
    assert item["usage"]["prompt_tokens"] == 11
    assert item["usage"]["completion_tokens"] == 5
    assert isinstance(item["ttft_seconds"], float)
    assert item["answer_length"] == len("private financial answer")
    serialized = json.dumps(report, ensure_ascii=False)
    assert "private financial answer" not in serialized
    assert "temporary-secret" not in serialized


def test_hermes_runs_accepts_one_full_json_fence_without_weakening_trace_validation(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    payload = {"answer_audit_trace": _trace_map()[case_id]}

    class FencedHermesTransport(FakeHermesTransport):
        def request(self, *, url: str, body: bytes, headers: dict[str, str], timeout: float):
            response = super().request(url=url, body=body, headers=headers, timeout=timeout)
            if body:
                return response
            event_payload = {
                "event": "run.completed",
                "output": "```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```",
            }
            data = f"data: {json.dumps(event_payload, ensure_ascii=False)}\n\n".encode()
            return live.TransportResponse(200, {"content-type": "text/event-stream"}, io.BytesIO(data))

    report = live.run_live_benchmark(
        config=live.LiveModelConfig(
            mode="live-http",
            endpoint="https://hermes.test/v1/runs",
            protocol="hermes-runs",
        ),
        case_root=_case_root(tmp_path, [case_id]),
        transport=FencedHermesTransport(payload),
    )

    assert report["passed"] is True

    invalid = live._json_object('explanation\n```json\n{"answer_audit_trace": {}}\n```')
    assert invalid is None


def test_auto_protocol_recognizes_hermes_runs_endpoint(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    transport = FakeHermesTransport({"answer_audit_trace": _trace_map()[case_id]})

    report = live.run_live_benchmark(
        config=live.LiveModelConfig(
            mode="live-http",
            endpoint="http://hermes.test/v1/runs/",
            protocol="auto",
            model="siq_assistant",
        ),
        case_root=_case_root(tmp_path, [case_id]),
        transport=transport,
    )

    assert report["passed"] is True
    assert "input" in json.loads(transport.requests[0]["body"])
    assert transport.requests[1]["url"] == "http://hermes.test/v1/runs/run_financial_qa_1/events"


def test_siq_chat_protocol_verifies_runtime_owned_audit_trace(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    transport = FakeSIQChatTransport(case_id)
    report = live.run_live_benchmark(
        config=live.LiveModelConfig(
            mode="live-http",
            endpoint="http://siq.test/api/chat",
            protocol="siq-chat",
            auth_token="temporary-secret",
        ),
        case_root=_case_root(tmp_path, [case_id]),
        transport=transport,
    )

    assert report["passed"] is True
    assert len(transport.requests) == 2
    request = json.loads(transport.requests[0]["body"])
    assert request["display_message"] == "腾讯 2025 年营业收入是多少？请给出来源。"
    assert request["context"]["research_identity"]["company_id"] == "HK:00700"
    assert transport.requests[1]["url"] == "http://siq.test/api/chat/audit-traces/aat_" + "a" * 32
    assert transport.requests[1]["body"] == b""
    assert report["results"][0]["ttft_seconds"] == live.UNAVAILABLE
    assert report["results"][0]["answer_length"] == len("private runtime answer")
    serialized = json.dumps(report)
    assert "private runtime answer" not in serialized
    assert "temporary-secret" not in serialized


def test_live_gate_excludes_synthetic_attack_traces_without_attack_stimulus(tmp_path):
    factual_case = "p0-hk-00700-2025-revenue"
    synthetic_case = "p0-hk-01398-2025-revenue-value-mismatch"
    transport = FakeSIQChatTransport(factual_case)
    report = live.run_live_benchmark(
        config=live.LiveModelConfig(
            mode="live-http",
            endpoint="http://siq.test/api/chat",
            protocol="siq-chat",
            auth_token="temporary-secret",
        ),
        case_root=_case_root(tmp_path, [factual_case, synthetic_case]),
        case_ids=[factual_case, synthetic_case],
        transport=transport,
    )

    assert report["summary"]["cases"] == 1
    assert report["summary"]["synthetic_attack_cases_excluded"] == 1
    assert [item["case_id"] for item in report["results"]] == [factual_case]


def test_hermes_runs_rejects_unsafe_run_id_before_events_request(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    transport = FakeHermesTransport(_payload(case_id), run_id="../events?token=secret")

    report = live.run_live_benchmark(
        config=live.LiveModelConfig(
            mode="live-http",
            endpoint="http://hermes.test/v1/runs",
            protocol="hermes-runs",
        ),
        case_root=_case_root(tmp_path, [case_id]),
        transport=transport,
    )

    assert report["passed"] is False
    assert len(transport.requests) == 1
    assert report["results"][0]["errors"] == ["hermes_missing_run_id"]
    assert "secret" not in json.dumps(report)


def test_hermes_failed_terminal_is_reported_without_upstream_error_text(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    transport = FakeHermesTransport({"error": "provider token=secret"}, event="run.failed")

    report = live.run_live_benchmark(
        config=live.LiveModelConfig(
            mode="live-http",
            endpoint="http://hermes.test/v1/runs",
            protocol="hermes-runs",
        ),
        case_root=_case_root(tmp_path, [case_id]),
        transport=transport,
    )

    assert report["passed"] is False
    assert report["results"][0]["errors"] == ["hermes_run_failed"]
    assert "provider" not in json.dumps(report)
    assert "secret" not in json.dumps(report)


def test_live_evidence_failure_is_a_real_case_failure(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    payload = _payload(case_id)
    payload["answer_audit_trace"] = dict(payload["answer_audit_trace"])
    payload["answer_audit_trace"]["wiki_facts"] = []
    transport = FakeTransport({case_id: payload})
    report = live.run_live_benchmark(
        config=live.LiveModelConfig(mode="live-http", endpoint="http://fake.test/chat", protocol="json"),
        case_root=_case_root(tmp_path, [case_id]),
        transport=transport,
    )
    assert report["passed"] is False
    assert any("expected wiki_facts" in error for error in report["results"][0]["errors"])


def test_live_trace_with_facts_but_wrong_canonical_envelope_fails(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"
    payload = _payload(case_id)
    trace = dict(payload["answer_audit_trace"])
    trace["question_id"] = "wrong-case"
    trace.pop("query_plan")
    payload["answer_audit_trace"] = trace
    report = live.run_live_benchmark(
        config=live.LiveModelConfig(mode="live-http", endpoint="http://fake.test/chat", protocol="json"),
        case_root=_case_root(tmp_path, [case_id]),
        transport=FakeTransport({case_id: payload}),
    )

    assert report["passed"] is False
    assert any("question_id expected" in error for error in report["results"][0]["errors"])
    assert "answer_audit_trace.query_plan must be an object" in report["results"][0]["errors"]


def test_live_timeout_and_protocol_eof_are_sanitized(tmp_path):
    case_id = "p0-hk-00700-2025-revenue"

    class TimeoutTransport:
        def request(self, **kwargs):
            raise TimeoutError("upstream token=secret")

    timeout_report = live.run_live_benchmark(
        config=live.LiveModelConfig(mode="live-http", endpoint="http://fake.test/chat", protocol="json"),
        case_root=_case_root(tmp_path, [case_id]),
        transport=TimeoutTransport(),
    )
    assert timeout_report["passed"] is False
    assert "secret" not in json.dumps(timeout_report)

    eof_transport = FakeTransport({case_id: _payload(case_id)}, sse=True)
    eof_transport.request = lambda **kwargs: live.TransportResponse(200, {"content-type": "text/event-stream"}, io.BytesIO(b"data: {\"delta\":\"partial\"}\n\n"))
    eof_report = live.run_live_benchmark(
        config=live.LiveModelConfig(mode="live-http", endpoint="http://fake.test/chat", protocol="sse"),
        case_root=_case_root(tmp_path, [case_id]),
        transport=eof_transport,
    )
    assert eof_report["results"][0]["errors"] == ["protocol_eof"]


def test_live_mode_requires_explicit_endpoint(tmp_path):
    with pytest.raises(live.LiveModelError, match="missing_or_invalid_endpoint"):
        live.run_live_benchmark(config=live.LiveModelConfig(mode="live-http"), case_root=tmp_path)


def test_required_report_validator_rejects_legacy_not_run_pass_shape():
    legacy_report = {
        "status": "not_run",
        "passed": True,
        "summary": {"cases": 0, "passed_cases": 0},
        "results": [],
    }

    assert live.required_live_execution_satisfied(legacy_report) is False


def test_cli_required_disabled_mode_writes_blocked_report_and_fails(tmp_path):
    output = tmp_path / "live.json"
    markdown = tmp_path / "live.md"

    exit_code = live.main(
        [
            "--mode",
            "disabled",
            "--required",
            "--case-root",
            str(tmp_path),
            "--output",
            str(output),
            "--markdown",
            str(markdown),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 1
    assert report["status"] == "blocked"
    assert report["passed"] is False
    assert report["required_execution_satisfied"] is False
    assert report["reason"] == "required_live_benchmark_not_run"


def test_cli_optional_disabled_mode_is_non_passing_but_skippable(tmp_path):
    output = tmp_path / "live.json"

    exit_code = live.main(
        [
            "--mode",
            "disabled",
            "--case-root",
            str(tmp_path),
            "--output",
            str(output),
            "--markdown",
            str(tmp_path / "live.md"),
        ]
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert report["status"] == "not_run"
    assert report["passed"] is False
    assert report["required_execution_satisfied"] is False
    assert "Live endpoint: not invoked" in (tmp_path / "live.md").read_text(encoding="utf-8")
