#!/usr/bin/env python3
"""Live-model financial QA benchmark with an explicit network boundary.

Standalone and contract/dev runs default to disabled and never construct a
network request.  The formal release wrapper invokes ``--mode live-http
--required`` and accepts only a non-empty, fully passing live execution.  A
live response must expose the same answer-audit trace contract as the offline
benchmark so values, evidence, ResearchIdentity and attack refusals are
checked by the existing verifier.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Protocol
from urllib.parse import quote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "scripts" / "maintenance") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts" / "maintenance"))

from run_financial_qa_benchmark import (  # noqa: E402
    DEFAULT_CASE_ROOT,
    case_modes,
    evaluate_trace_case,
    load_cases,
    validate_case,
)

DEFAULT_OUTPUT = REPO_ROOT / "artifacts" / "eval-runs" / "financial-qa" / "live_financial_qa_benchmark.json"
DEFAULT_MARKDOWN = REPO_ROOT / "artifacts" / "eval-runs" / "financial-qa" / "live_financial_qa_benchmark.md"
LIVE_MODES = ("disabled", "live-http")
LIVE_PROTOCOLS = ("auto", "json", "sse", "hermes-runs", "siq-chat")
SCHEMA_VERSION = "siq_live_financial_qa_benchmark_v1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
UNAVAILABLE = "unavailable"
AUDIT_TRACE_ID_RE = re.compile(r"^aat_[a-f0-9]{32}$")
SYNTHETIC_ATTACK_INTENTS = frozenset(
    {
        "metric_lookup_attack",
        "metric_lookup_identity_attack",
        "metric_calculation_trace_attack",
    }
)


class LiveModelError(RuntimeError):
    """A sanitized transport or protocol failure."""


class LiveModelTransport(Protocol):
    def request(self, *, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> "TransportResponse": ...


@dataclass
class TransportResponse:
    status: int
    headers: Mapping[str, str]
    body: Any


@dataclass(frozen=True)
class LiveModelConfig:
    mode: str = "disabled"
    endpoint: str = ""
    protocol: str = "auto"
    timeout: float = 60.0
    model: str = ""
    auth_token: str = ""
    required: bool = False


def _http_origin(url: str) -> tuple[str, str, int]:
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise LiveModelError("unsafe_redirect")
    try:
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError as exc:
        raise LiveModelError("unsafe_redirect") from exc
    return scheme, parsed.hostname.lower(), port


class SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow redirects only when the authorization boundary is unchanged."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        if _http_origin(req.full_url) != _http_origin(newurl):
            raise LiveModelError("unsafe_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class UrllibTransport:
    """Small injectable HTTP transport; the runner itself remains sync."""

    def request(self, *, url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> TransportResponse:
        # Hermes Runs uses the same transport boundary for POST /v1/runs and
        # GET /v1/runs/{run_id}/events.  An empty body is reserved for GET;
        # generic JSON/SSE callers continue to use a non-empty POST body.
        method = "GET" if not body else "POST"
        request = urllib.request.Request(url, data=body or None, method=method, headers=dict(headers))
        try:
            response = urllib.request.build_opener(SameOriginRedirectHandler()).open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            payload = exc.read(64 * 1024)
            raise LiveModelError(f"http_status_{exc.code}: {redact_text(payload.decode('utf-8', 'replace'))}") from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
            raise LiveModelError(f"transport_error: {redact_text(str(exc))}") from exc
        return TransportResponse(status=int(response.status), headers=dict(response.headers.items()), body=response)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def redact_text(value: str, *, limit: int = 240) -> str:
    """Return a short diagnostic without tokens, URLs, or model answer text."""
    text = str(value or "")
    text = text.replace("\n", " ").replace("\r", " ")
    text = re.sub(
        r'''(?ix)(["']?authorization["']?\s*[:=]\s*)'''
        r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:bearer|basic)\s+[^\s&;,]+|[^\s&;,]+)''',
        r"\1[redacted]",
        text,
    )
    text = re.sub(
        r'''(?ix)(["']?(?:access_token|source_token|api_key|token)["']?\s*[:=]\s*)'''
        r'''(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|(?:(?:bearer|basic)\s+)?[^\s&;,]+)''',
        r"\1[redacted]",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[^\s&;,]+", "Bearer [redacted]", text)
    parsed = urlsplit(text)
    if parsed.scheme and parsed.netloc:
        text = f"{parsed.scheme}://[redacted]"
    return text[:limit]


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _header(response: TransportResponse, name: str) -> str:
    for key, value in response.headers.items():
        if str(key).lower() == name.lower():
            return str(value)
    return ""


def _json_object(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    if isinstance(raw, str):
        candidate = raw
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            # Some model gateways preserve a single JSON markdown fence even
            # when the prompt requests raw JSON.  Accept only a fence that
            # wraps the entire response; prose before/after it remains an
            # invalid protocol response and semantic checks are unchanged.
            fenced = re.fullmatch(r"\s*```(?:json)?\s*(\{.*\})\s*```\s*", candidate, flags=re.DOTALL | re.IGNORECASE)
            if fenced is None:
                return None
            try:
                value = json.loads(fenced.group(1))
            except json.JSONDecodeError:
                return None
        return value if isinstance(value, dict) else None
    return None


def _read_response_body(response: TransportResponse) -> Any:
    body = response.body.read(MAX_RESPONSE_BYTES + 1) if hasattr(response.body, "read") else response.body
    if isinstance(body, (bytes, str)) and len(body) > MAX_RESPONSE_BYTES:
        raise LiveModelError("response_body_too_large")
    return body


def _event_is_terminal(payload: Mapping[str, Any], event_type: str = "") -> bool:
    normalized = str(event_type or payload.get("type") or payload.get("event") or payload.get("status") or "").lower()
    return normalized in {
        "done",
        "completed",
        "succeeded",
        "failed",
        "cancelled",
        "canceled",
        "run.completed",
        "run.failed",
        "run.cancelled",
        "run.canceled",
    } or normalized.endswith((".completed", ".succeeded", ".failed", ".cancelled", ".canceled"))


def _payload_output_text(payload: Mapping[str, Any]) -> str:
    value = payload.get("output")
    if isinstance(value, str):
        parsed = _json_object(value)
        if parsed is not None:
            structured_text = _payload_output_text(parsed)
            if structured_text:
                return structured_text
        return value
    if isinstance(value, dict):
        structured_text = _payload_output_text(value)
        if structured_text:
            return structured_text
    for key in ("answer", "reply", "text"):
        value = payload.get(key)
        if isinstance(value, str):
            return value
    return ""


def _payload_candidates(payload: Mapping[str, Any]) -> Iterable[dict[str, Any]]:
    yield dict(payload)
    for key in ("data", "result", "response", "output", "final"):
        value = payload.get(key)
        if isinstance(value, dict):
            yield value
        elif isinstance(value, str):
            # Hermes run.completed carries output as a string.  A benchmark
            # response may serialize the canonical trace inside that field;
            # parse only structured JSON and never persist the raw answer.
            parsed = _json_object(value)
            if parsed is not None:
                yield parsed


def extract_trace(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """Extract the canonical trace without interpreting free-form answer text."""
    for candidate in _payload_candidates(payload):
        for key in ("answer_audit_trace", "audit_trace", "trace"):
            trace = candidate.get(key)
            if isinstance(trace, dict):
                return dict(trace)
        if any(key in candidate for key in ("resolved_company", "wiki_facts", "postgres_facts", "guardrail_result")):
            return candidate
    return None


def _stream_events(stream: Any, *, deadline: float) -> tuple[dict[str, Any], float, float, str]:
    """Read SSE lines and return final payload, TTFT, total latency, and text hash."""
    started = time.monotonic()
    first_token: float | None = None
    chunks: list[str] = []
    final: dict[str, Any] | None = None
    event_data: list[str] = []
    event_type = ""
    event_received_at: float | None = None
    received_bytes = 0
    while True:
        raw_line = stream.readline()
        if raw_line in (b"", ""):
            break
        if time.monotonic() - started > deadline:
            raise LiveModelError("timeout")
        received_bytes += len(raw_line.encode("utf-8") if isinstance(raw_line, str) else raw_line)
        if received_bytes > MAX_RESPONSE_BYTES:
            raise LiveModelError("response_body_too_large")
        line = raw_line.decode("utf-8", "replace") if isinstance(raw_line, bytes) else str(raw_line)
        line = line.rstrip("\r\n")
        if not line:
            if event_data:
                for item in event_data:
                    parsed = _json_object(item) if item.strip() != "[DONE]" else None
                    if parsed:
                        candidate = parsed.get("answer_audit_trace") or parsed.get("audit_trace") or parsed
                        terminal = _event_is_terminal(parsed, event_type)
                        if isinstance(candidate, dict) and (
                            terminal or "done" in parsed or "trace" in parsed or "answer_audit_trace" in parsed
                        ):
                            final = parsed
                        if terminal and _payload_output_text(parsed) and first_token is None:
                            first_token = event_received_at or time.monotonic()
                        if isinstance(parsed.get("delta"), str):
                            delta = parsed["delta"]
                            if delta and first_token is None:
                                first_token = event_received_at or time.monotonic()
                            chunks.append(delta)
                event_data.clear()
                event_type = ""
                event_received_at = None
            continue
        if line.startswith("event:"):
            event_type = line[6:].strip().lower()
            continue
        if line.startswith("data:"):
            if event_received_at is None:
                event_received_at = time.monotonic()
            event_data.append(line[5:].lstrip())
    if event_data:
        for item in event_data:
            parsed = _json_object(item) if item.strip() != "[DONE]" else None
            if parsed:
                candidate = parsed.get("answer_audit_trace") or parsed.get("audit_trace") or parsed
                terminal = _event_is_terminal(parsed, event_type)
                if isinstance(candidate, dict) and (
                    terminal or "done" in parsed or "trace" in parsed or "answer_audit_trace" in parsed
                ):
                    final = parsed
                if terminal and _payload_output_text(parsed) and first_token is None:
                    first_token = event_received_at or time.monotonic()
                if isinstance(parsed.get("delta"), str):
                    delta = parsed["delta"]
                    if delta and first_token is None:
                        first_token = event_received_at or time.monotonic()
                    chunks.append(delta)
    if final is None:
        raise LiveModelError("protocol_eof")
    text = "".join(chunks) or _payload_output_text(final)
    final["__answer_length"] = len(text)
    return final, (first_token - started if first_token is not None else None), time.monotonic() - started, _hash_text(text)


def parse_response(response: TransportResponse, *, protocol: str, timeout: float) -> tuple[dict[str, Any], float | None, float, str]:
    content_type = _header(response, "content-type").lower()
    use_sse = protocol == "sse" or (protocol == "auto" and "text/event-stream" in content_type)
    if use_sse:
        return _stream_events(response.body, deadline=timeout)
    started = time.monotonic()
    body = _read_response_body(response)
    payload = _json_object(body)
    if payload is None:
        raise LiveModelError("invalid_json_response")
    text = str(payload.get("answer") or payload.get("reply") or payload.get("text") or "")
    elapsed = time.monotonic() - started
    return payload, elapsed, elapsed, _hash_text(text)


def _hermes_run_request(case: Mapping[str, Any], *, model: str) -> dict[str, Any]:
    """Build the supported Hermes Runs API payload.

    Hermes accepts ``model``, ``input`` and optional ``session_id``.  The
    output contract is requested in the input because the Runs API does not
    define a response_format field and returns the completed output as text.
    """
    question = str(case.get("question") or "")
    identity = {
        key: case.get(key)
        for key in (
            "market",
            "company_id",
            "ticker",
            "company_name",
            "report_id",
            "filing_id",
            "parse_run_id",
            "period",
            "fiscal_year",
        )
        if case.get(key) not in (None, "")
    }
    source_policy = case.get("source_policy") if isinstance(case.get("source_policy"), dict) else {}
    trace_skeleton = {
        "answer_audit_trace": {
            "schema_version": "siq_answer_audit_trace_v1",
            "question_id": case.get("case_id"),
            "resolved_company": {
                "market": identity.get("market"),
                "id": identity.get("company_id"),
                "name": identity.get("company_name"),
                "code": identity.get("ticker"),
            },
            "resolved_period": {
                key: identity.get(key)
                for key in ("fiscal_year", "period", "filing_id", "report_id", "parse_run_id")
                if identity.get(key) not in (None, "")
            },
            "query_plan": {
                "mode": "wiki_first",
                "observed_source_types": [],
                "allow_postgres_fallback": source_policy.get("allow_postgres_fallback", True) is not False,
            },
            "wiki_facts": [],
            "postgres_facts": [],
            "fallback_reason": None,
            "calculator_runs": [],
            "citations": [],
            "claim_verifier_result": {"violations": []},
            "guardrail_result": {
                "blocked": False,
                "reason": None,
                "output_was_guarded": True,
            },
        }
    }
    fallback_reasons = source_policy.get("allowed_fallback_reasons")
    if not isinstance(fallback_reasons, list):
        fallback_reasons = []
    contract = (
        "\n\nAuthoritative SIQ ResearchIdentity for this benchmark case (do not infer or replace fields): "
        + json.dumps(identity, ensure_ascii=False, sort_keys=True)
        + "."
        "\n\nReturn only one JSON object matching this canonical field skeleton: "
        + json.dumps(trace_skeleton, ensure_ascii=False, sort_keys=True)
        + ". Preserve the supplied identity values exactly. Populate wiki_facts and postgres_facts only "
        "with facts actually returned by SIQ tools. Each fact must include source_type, metric or concept, "
        "statement_type, period, value, raw_value, unit/currency/scale when applicable, and its reviewable "
        "evidence fields such as task_id, table_index, source_page, html_anchor, quote or quote_text. "
        "Use Wiki metrics/evidence first. PostgreSQL facts are allowed only when the source policy permits "
        "fallback and fallback_reason is one of: "
        + json.dumps(fallback_reasons, ensure_ascii=False)
        + ". Never use semantic/vector retrieval as the numeric source. Derived values must include a "
        "calculator_runs entry with operation, inputs/formula, result, unit and currency. Citations must "
        "reference the facts actually used. If evidence is missing, conflicts with ResearchIdentity, a "
        "claimed value conflicts with evidence, or a required calculation trace is missing, do not invent "
        "data: set guardrail_result.blocked=true, set a concrete reason, and record any claim violations. "
        "For an answer that passes these checks, keep guardrail_result.blocked=false. Do not include "
        "markdown fences or explanatory text outside the JSON object."
    )
    return {
        "model": model or "hermes-agent",
        "input": question + contract,
        "session_id": f"siq-financial-qa-{case.get('case_id')}",
    }


def _siq_chat_request(case: Mapping[str, Any]) -> dict[str, Any]:
    """Build a request for the real SIQ API runtime and its audit callback."""
    identity = {
        key: case.get(key)
        for key in ("market", "company_id", "filing_id", "parse_run_id")
        if case.get(key) not in (None, "")
    }
    company = {
        key: value
        for key, value in {
            "market": case.get("market"),
            "company_id": case.get("company_id"),
            "filing_id": case.get("filing_id"),
            "parse_run_id": case.get("parse_run_id"),
            "code": case.get("ticker"),
            "name": case.get("company_name"),
        }.items()
        if value not in (None, "")
    }
    question = str(case.get("question") or "")
    return {
        "message": f"question_id={case.get('case_id')}\n{question}",
        "display_message": question,
        "context": {
            "company": company,
            "research_identity": identity,
            **identity,
        },
        "attachments": [],
    }


def _siq_audit_trace_url(endpoint: str, trace_id: str) -> str:
    if not AUDIT_TRACE_ID_RE.fullmatch(trace_id):
        raise LiveModelError("siq_chat_missing_audit_trace_id")
    parsed = urlsplit(endpoint)
    path = parsed.path.rstrip("/")
    if not path.endswith("/chat"):
        raise LiveModelError("siq_chat_invalid_endpoint")
    audit_path = path + "/audit-traces/" + quote(trace_id, safe="")
    return parsed._replace(path=audit_path, query="", fragment="").geturl()


def case_request(case: Mapping[str, Any], *, stream: bool) -> dict[str, Any]:
    identity = {
        key: case.get(key)
        for key in ("market", "company_id", "ticker", "company_name", "report_id", "filing_id", "period", "fiscal_year")
        if case.get(key) not in (None, "")
    }
    return {
        "schema_version": "siq_live_financial_qa_request_v1",
        "case_id": case.get("case_id"),
        "question": case.get("question"),
        "research_identity": identity,
        "response_format": "answer_audit_trace_v1",
        "stream": bool(stream),
    }


def _usage(payload: Mapping[str, Any]) -> dict[str, Any]:
    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    def available(value: Any) -> Any:
        return value if value not in (None, "") else UNAVAILABLE

    return {
        "prompt_tokens": available(usage.get("prompt_tokens", usage.get("input_tokens"))),
        "completion_tokens": available(usage.get("completion_tokens", usage.get("output_tokens"))),
        "total_tokens": available(usage.get("total_tokens")),
        "cost": available(payload.get("cost", usage.get("cost"))),
    }


def _model_identity(payload: Mapping[str, Any], config: LiveModelConfig) -> str:
    for key in ("model", "model_name", "deployment", "model_identity", "provider_model"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return config.model or UNAVAILABLE


def _terminal_failure(payload: Mapping[str, Any]) -> str | None:
    event_type = str(payload.get("event") or payload.get("type") or payload.get("status") or "").lower()
    if event_type in {"run.failed", "failed"} or event_type.endswith(".failed"):
        return "hermes_run_failed"
    if event_type in {"run.cancelled", "run.canceled", "cancelled", "canceled"} or event_type.endswith(
        (".cancelled", ".canceled")
    ):
        return "hermes_run_cancelled"
    return None


def run_live_benchmark(
    *,
    config: LiveModelConfig,
    case_root: Path = DEFAULT_CASE_ROOT,
    transport: LiveModelTransport | None = None,
    case_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    if config.mode == "disabled":
        if config.required:
            raise LiveModelError("required_live_benchmark_not_run")
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": now_iso(),
            "status": "not_run",
            "passed": False,
            "live_execution_completed": False,
            "execution": {"mode": "disabled", "case_attempts": 0, "network_requests_started": 0},
            "reason": "live-model benchmark is disabled by default",
            "summary": {"cases": 0, "passed_cases": 0},
            "results": [],
        }
    if config.mode != "live-http":
        raise ValueError(f"unsupported live benchmark mode: {config.mode}")
    parsed = urlsplit(config.endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
        raise LiveModelError("missing_or_invalid_endpoint")
    if config.required and parsed.scheme != "https":
        raise LiveModelError("required_live_benchmark_requires_https")
    if config.required and not config.auth_token.strip():
        raise LiveModelError("required_live_benchmark_missing_auth_token")
    selected_case_ids = frozenset(str(item).strip() for item in (case_ids or ()) if str(item).strip())
    all_trace_cases = [case for case in load_cases(case_root) if "trace-offline" in case_modes(case)]
    synthetic_cases = [case for case in all_trace_cases if str(case.get("intent") or "") in SYNTHETIC_ATTACK_INTENTS]
    cases = [
        case
        for case in all_trace_cases
        if str(case.get("intent") or "") not in SYNTHETIC_ATTACK_INTENTS
        and (not selected_case_ids or str(case.get("case_id") or "") in selected_case_ids)
    ]
    transport = transport or UrllibTransport()
    results: list[dict[str, Any]] = []
    network_requests_started = 0
    for case in cases:
        started = time.monotonic()
        item: dict[str, Any] = {
            "case_id": case.get("case_id"),
            "market": case.get("market"),
            "model": UNAVAILABLE,
            "ttft_seconds": UNAVAILABLE,
            "total_latency_seconds": UNAVAILABLE,
            "answer_sha256": UNAVAILABLE,
            "answer_length": UNAVAILABLE,
            "usage": {
                "prompt_tokens": UNAVAILABLE,
                "completion_tokens": UNAVAILABLE,
                "total_tokens": UNAVAILABLE,
                "cost": UNAVAILABLE,
            },
        }
        errors = validate_case(case)
        if errors:
            item.update({"passed": False, "errors": ["invalid_case"]})
            results.append(item)
            continue
        try:
            endpoint_path = parsed.path.rstrip("/")
            hermes_protocol = config.protocol == "hermes-runs" or (
                config.protocol == "auto" and endpoint_path.endswith("/v1/runs")
            )
            siq_chat_protocol = config.protocol == "siq-chat"
            body_payload = (
                _hermes_run_request(case, model=config.model)
                if hermes_protocol
                else _siq_chat_request(case)
                if siq_chat_protocol
                else case_request(case, stream=config.protocol != "json")
            )
            body = json.dumps(body_payload, ensure_ascii=False).encode("utf-8")
            headers = {"Content-Type": "application/json", "Accept": "text/event-stream, application/json"}
            if config.auth_token:
                headers["Authorization"] = f"Bearer {config.auth_token}"
            network_requests_started += 1
            response = transport.request(url=config.endpoint, body=body, headers=headers, timeout=config.timeout)
            transport_elapsed = time.monotonic() - started
            if response.status < 200 or response.status >= 300:
                raise LiveModelError(f"http_status_{response.status}")
            if siq_chat_protocol:
                if not config.auth_token:
                    raise LiveModelError("siq_chat_missing_auth_token")
                chat_payload = _json_object(_read_response_body(response))
                if chat_payload is None:
                    raise LiveModelError("invalid_json_response")
                reply = chat_payload.get("reply")
                if not isinstance(reply, str):
                    raise LiveModelError("siq_chat_missing_reply")
                trace_id = str(chat_payload.get("audit_trace_id") or "").strip()
                network_requests_started += 1
                trace_response = transport.request(
                    url=_siq_audit_trace_url(config.endpoint, trace_id),
                    body=b"",
                    headers={**headers, "Accept": "application/json"},
                    timeout=config.timeout,
                )
                if trace_response.status < 200 or trace_response.status >= 300:
                    raise LiveModelError(f"http_status_{trace_response.status}")
                trace_payload = _json_object(_read_response_body(trace_response))
                trace = trace_payload.get("trace") if trace_payload else None
                if not isinstance(trace, dict):
                    raise LiveModelError("siq_chat_missing_answer_audit_trace")
                payload = {
                    "answer_audit_trace": trace,
                    "__answer_length": len(reply),
                }
                response_ttft = None
                answer_hash = _hash_text(reply)
                transport_elapsed = time.monotonic() - started
            elif hermes_protocol:
                # The first response creates the run; its identifier is used
                # only to construct the event URL and is never reported.
                create_payload = _json_object(_read_response_body(response))
                run_id = create_payload.get("run_id") if create_payload else None
                if not isinstance(run_id, str) or not run_id.strip() or "/" in run_id or "\\" in run_id:
                    raise LiveModelError("hermes_missing_run_id")
                events_url = config.endpoint.rstrip("/") + "/" + quote(run_id.strip(), safe="") + "/events"
                network_requests_started += 1
                event_response = transport.request(
                    url=events_url,
                    body=b"",
                    headers={**headers, "Accept": "text/event-stream"},
                    timeout=config.timeout,
                )
                if event_response.status < 200 or event_response.status >= 300:
                    raise LiveModelError(f"http_status_{event_response.status}")
                transport_elapsed = time.monotonic() - started
                payload, response_ttft, _response_total, answer_hash = _stream_events(
                    event_response.body,
                    deadline=config.timeout,
                )
            else:
                payload, response_ttft, _response_total, answer_hash = parse_response(
                    response,
                    protocol=config.protocol,
                    timeout=config.timeout,
                )
                # ``auto`` remains backwards compatible with direct JSON/SSE,
                # but follows a Hermes run when the POST response advertises a
                # run_id.  This lets existing endpoint configuration migrate
                # without silently treating the create response as an answer.
                if config.protocol == "auto" and isinstance(payload.get("run_id"), str):
                    run_id = payload["run_id"]
                    if not run_id.strip() or "/" in run_id or "\\" in run_id:
                        raise LiveModelError("hermes_missing_run_id")
                    events_url = config.endpoint.rstrip("/") + "/" + quote(run_id.strip(), safe="") + "/events"
                    network_requests_started += 1
                    event_response = transport.request(
                        url=events_url,
                        body=b"",
                        headers={**headers, "Accept": "text/event-stream"},
                        timeout=config.timeout,
                    )
                    if event_response.status < 200 or event_response.status >= 300:
                        raise LiveModelError(f"http_status_{event_response.status}")
                    transport_elapsed = time.monotonic() - started
                    payload, response_ttft, _response_total, answer_hash = _stream_events(
                        event_response.body,
                        deadline=config.timeout,
                    )
            total = time.monotonic() - started
            ttft = transport_elapsed + response_ttft if response_ttft is not None else UNAVAILABLE
            terminal_failure = _terminal_failure(payload)
            if terminal_failure:
                raise LiveModelError(terminal_failure)
            trace = extract_trace(payload)
            item.update(
                {
                    "model": _model_identity(payload, config),
                    "ttft_seconds": ttft,
                    "total_latency_seconds": total,
                    "answer_sha256": answer_hash,
                    "answer_length": payload.get("__answer_length", len(str(payload.get("answer") or payload.get("reply") or payload.get("text") or ""))),
                    "usage": _usage(payload),
                }
            )
            if trace is None:
                raise LiveModelError("missing_answer_audit_trace")
            checked = evaluate_trace_case(case, trace)
            item.update({"passed": bool(checked.get("passed")), "verification": {"facts": checked.get("facts", []), "guardrail_blocked": checked.get("guardrail_blocked", False)}, "errors": [redact_text(error) for error in checked.get("errors", [])]})
        except (LiveModelError, TimeoutError, socket.timeout, OSError) as exc:
            item.update({"passed": False, "errors": [redact_text(str(exc))], "total_latency_seconds": time.monotonic() - started})
        results.append(item)
    passed_cases = sum(1 for item in results if item.get("passed"))
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at": now_iso(),
        "status": "completed",
        "passed": bool(results) and passed_cases == len(results),
        "live_execution_completed": bool(results) and network_requests_started >= len(results),
        "execution": {
            "mode": "live-http",
            "case_attempts": len(results),
            "network_requests_started": network_requests_started,
        },
        "endpoint": f"{parsed.scheme}://{parsed.hostname or '[redacted]'}{parsed.path}",
        "summary": {
            "cases": len(results),
            "passed_cases": passed_cases,
            "failed_cases": len(results) - passed_cases,
            "synthetic_attack_cases_excluded": len(synthetic_cases),
            "case_filter_count": len(selected_case_ids),
        },
        "results": results,
    }


def required_live_execution_satisfied(report: Mapping[str, Any]) -> bool:
    """Return whether a report proves a non-empty, fully passing live run."""
    summary = report.get("summary") if isinstance(report.get("summary"), Mapping) else {}
    execution = report.get("execution") if isinstance(report.get("execution"), Mapping) else {}
    results = report.get("results") if isinstance(report.get("results"), list) else []
    cases = summary.get("cases")
    network_requests = execution.get("network_requests_started")
    return (
        report.get("status") == "completed"
        and report.get("passed") is True
        and report.get("live_execution_completed") is True
        and isinstance(cases, int)
        and not isinstance(cases, bool)
        and cases > 0
        and summary.get("passed_cases") == cases
        and summary.get("failed_cases") == 0
        and len(results) == cases
        and all(isinstance(item, Mapping) and item.get("passed") is True for item in results)
        and execution.get("mode") == "live-http"
        and isinstance(network_requests, int)
        and not isinstance(network_requests, bool)
        and network_requests >= cases
    )


def render_markdown(report: Mapping[str, Any]) -> str:
    summary = report.get("summary") or {}
    endpoint_line = (
        "- Live endpoint: configured (URL path only; credentials and answer text omitted)"
        if report.get("status") == "completed"
        else "- Live endpoint: not invoked"
    )
    lines = [
        "# Live Financial QA Benchmark",
        "",
        f"Status: **{str(report.get('status', 'unknown')).upper()}**",
        f"Result: **{'PASS' if report.get('passed') else 'FAIL'}**",
        "",
        f"- Cases: {summary.get('passed_cases', 0)}/{summary.get('cases', 0)}",
        endpoint_line,
        "",
        "| Case | Market | Status | TTFT (s) | Total (s) | Model | Error |",
        "| --- | --- | --- | ---: | ---: | --- | --- |",
    ]
    for item in report.get("results") or []:
        lines.append(
            f"| {item.get('case_id')} | {item.get('market')} | {'PASS' if item.get('passed') else 'FAIL'} | "
            f"{item.get('ttft_seconds') if item.get('ttft_seconds') is not None else 'unavailable'} | "
            f"{item.get('total_latency_seconds') if item.get('total_latency_seconds') is not None else 'unavailable'} | "
            f"{item.get('model') or 'unavailable'} | {'; '.join(item.get('errors') or [])} |"
        )
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Opt-in live-model financial QA benchmark.")
    parser.add_argument("--mode", choices=LIVE_MODES, default=os.getenv("SIQ_LIVE_MODEL_BENCHMARK_MODE", "disabled"))
    parser.add_argument("--endpoint", default=os.getenv("SIQ_LIVE_MODEL_URL", ""))
    parser.add_argument("--protocol", choices=LIVE_PROTOCOLS, default=os.getenv("SIQ_LIVE_MODEL_PROTOCOL", "auto"))
    parser.add_argument("--timeout", type=float, default=float(os.getenv("SIQ_LIVE_MODEL_TIMEOUT", "60")))
    parser.add_argument("--model", default=os.getenv("SIQ_LIVE_MODEL_NAME", ""))
    parser.add_argument("--auth-token", default=os.getenv("SIQ_LIVE_MODEL_AUTH_TOKEN", ""), help=argparse.SUPPRESS)
    parser.add_argument("--required", action="store_true", default=os.getenv("SIQ_LIVE_MODEL_BENCHMARK_REQUIRED", "").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--case-root", type=Path, default=DEFAULT_CASE_ROOT)
    parser.add_argument("--case-id", action="append", default=[], help="Run only the named eligible live case; repeatable.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = LiveModelConfig(mode=args.mode, endpoint=args.endpoint, protocol=args.protocol, timeout=args.timeout, model=args.model, auth_token=args.auth_token, required=args.required)
    try:
        report = run_live_benchmark(config=config, case_root=args.case_root, case_ids=args.case_id)
    except LiveModelError as exc:
        report = {
            "schema_version": SCHEMA_VERSION,
            "created_at": now_iso(),
            "status": "blocked",
            "passed": False,
            "live_execution_completed": False,
            "execution": {"mode": config.mode, "case_attempts": 0, "network_requests_started": 0},
            "reason": redact_text(str(exc)),
            "summary": {"cases": 0, "passed_cases": 0},
            "results": [],
        }
    required_execution_satisfied = required_live_execution_satisfied(report)
    report["required_execution_satisfied"] = required_execution_satisfied
    if config.required and not required_execution_satisfied:
        report["passed"] = False
        report.setdefault("reason", "required_live_execution_not_satisfied")
    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    markdown = args.markdown if args.markdown.is_absolute() else REPO_ROOT / args.markdown
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    markdown.parent.mkdir(parents=True, exist_ok=True)
    markdown.write_text(render_markdown(report), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{'PASS' if report.get('passed') else 'BLOCKED' if report.get('status') == 'blocked' else 'FAIL'} live financial QA benchmark")
        print(f"JSON: {output}")
        print(f"Markdown: {markdown}")
    if report.get("status") == "not_run" and not config.required:
        return 0
    return 0 if report.get("passed") else (2 if report.get("status") == "blocked" and not config.required else 1)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
