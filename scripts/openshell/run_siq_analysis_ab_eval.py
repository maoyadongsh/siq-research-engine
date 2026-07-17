#!/usr/bin/env python3
"""Run an interleaved, no-cutover siq_analysis host/OpenShell A/B evaluation."""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import ipaddress
import json
import math
import os
import re
import socket
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

try:
    from scripts.openshell import check_sanitized_artifacts
except ModuleNotFoundError:  # direct execution from scripts/openshell
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from scripts.openshell import check_sanitized_artifacts


DATASET_SCHEMA_VERSION = "siq.openshell.siq-analysis-ab-dataset.v1"
RAW_SCHEMA_VERSION = "siq.openshell.siq-analysis-ab-raw.v2"
SUMMARY_SCHEMA_VERSION = "siq.openshell.siq-analysis-ab-summary.v3"
REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_DATASET_BYTES = 64 * 1024 * 1024
MAX_JSON_RESPONSE_BYTES = 1024 * 1024
MAX_SSE_EVENT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_SSE_EVENTS = 100_000
MIN_EVALUATION_CASES = 10
MIN_EVALUATION_REPETITIONS = 3
MIN_ARM_EXECUTIONS = MIN_EVALUATION_CASES * MIN_EVALUATION_REPETITIONS
MIN_PRIMARY_METRIC_SAMPLES = 10
MIN_FALLBACK_SAMPLES = MIN_EVALUATION_REPETITIONS
MIN_POLICY_NORMAL_SAMPLES = 20
MIN_LATENCY_SAMPLES = 20
PROVIDERS = (
    "siq-minimax-cn-pool",
    "siq-stepfun",
    "siq-kimi-coding",
    "siq-tavily-search",
)
DEFERRED_PROVIDERS = ("siq-exa-search",)
OPENSHELL_ABSOLUTE_QUALITY_FLOORS = {
    "task_success_rate": 0.95,
    "answer_citation_rate": 0.95,
    "numeric_accuracy": 0.95,
    "hallucination_block_rate": 0.95,
    "evidence_coverage": 0.95,
    "report_completeness": 0.95,
}
OPENSHELL_ABSOLUTE_RATE_CEILINGS = {
    "timeout_rate": 0.0,
    "policy_false_positive_rate": 0.0,
}
ALLOWED_PREEXISTING_EVAL_FILES = frozenset(
    {
        "case-plan.json",
        "dataset.json",
        "fallback-drill-plan.json",
        "host.key",
        "host-key-receipt.json",
        "host-runtime-receipt.json",
        "prerequisites.json",
        "provenance.json",
        "source-bindings.json",
    }
)
SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
SAFE_RUNTIME_LABEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,159}\Z")
RUN_ID_RE = re.compile(r"run_[A-Za-z0-9_-]{1,128}\Z")
ERROR_CODE_RE = re.compile(r"[a-z0-9][a-z0-9_.-]{0,95}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?%?")
HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$", re.MULTILINE)
POLICY_ERROR_RE = re.compile(r"(?:policy.*(?:denied|blocked)|(?:denied|blocked).*policy|immutable_write_blocked)")
DATASET_FIELDS = {
    "schema_version",
    "profile",
    "model",
    "temperature",
    "instructions",
    "repetitions",
    "run_timeout_seconds",
    "cases",
}
CASE_FIELDS = {"case_id", "input", "history", "expectations"}
MESSAGE_FIELDS = {"role", "content"}
EXPECTATION_FIELDS = {
    "numeric",
    "citations",
    "evidence_ids",
    "required_sections",
    "abstention_required",
    "abstention_markers",
    "required_tools",
    "fallback_expected",
    "policy_denial_expected",
}
NUMERIC_FIELDS = {"expectation_id", "value", "absolute_tolerance"}
TERMINAL_EVENTS = {"run.cancelled", "run.completed", "run.failed"}
FORBIDDEN_RAW_FIELDS = (
    b'"api_key"',
    b'"authorization"',
    b'"conversation_history"',
    b'"headers"',
    b'"input"',
    b'"instructions"',
    b'"output":',
    b'"prompt"',
)


class EvaluationConfigurationError(RuntimeError):
    """Stable configuration error that never includes prompt, key, or path content."""


class RunContractError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RunTimedOut(RunContractError):
    pass


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True)
class NumericExpectation:
    expectation_id: str
    value: float
    absolute_tolerance: float


@dataclass(frozen=True)
class CaseExpectations:
    numeric: tuple[NumericExpectation, ...]
    citations: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    required_sections: tuple[str, ...]
    abstention_required: bool
    abstention_markers: tuple[str, ...]
    required_tools: tuple[str, ...]
    fallback_expected: bool | None
    policy_denial_expected: bool


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    input_text: str
    history: tuple[dict[str, str], ...]
    expectations: CaseExpectations
    case_hash: str


@dataclass(frozen=True)
class EvaluationDataset:
    profile: str
    model: str
    temperature: float
    instructions: str
    repetitions: int
    run_timeout_seconds: float
    cases: tuple[EvaluationCase, ...]
    sha256: str


@dataclass
class RunObservation:
    status: str
    output: str = ""
    run_id: str = ""
    error_code: str = ""
    create_contract_ok: bool = False
    sse_contract_ok: bool = False
    terminal_contract_ok: bool = False
    stop_attempted: bool = False
    stop_contract_ok: bool | None = None
    ttft_ms: float | None = None
    total_duration_ms: float = 0.0
    event_counts: dict[str, int] | None = None
    successful_tools: set[str] | None = None
    failed_tools: set[str] | None = None
    tool_attempt_counts: dict[str, int] | None = None
    tool_success_counts: dict[str, int] | None = None
    tool_failure_counts: dict[str, int] | None = None
    fallback_activated: bool | None = None
    requested_model: str | None = None
    configured_provider: str | None = None
    configured_model: str | None = None
    effective_provider: str | None = None
    effective_model: str | None = None
    policy_denied: bool = False

    def __post_init__(self) -> None:
        if self.event_counts is None:
            self.event_counts = {}
        if self.successful_tools is None:
            self.successful_tools = set()
        if self.failed_tools is None:
            self.failed_tools = set()
        if self.tool_attempt_counts is None:
            self.tool_attempt_counts = {}
        if self.tool_success_counts is None:
            self.tool_success_counts = {}
        if self.tool_failure_counts is None:
            self.tool_failure_counts = {}


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate_json_key")
        value[key] = child
    return value


def _sha256(value: bytes | str) -> str:
    content = value.encode("utf-8") if isinstance(value, str) else value
    return hashlib.sha256(content).hexdigest()


def _safe_regular_file(path: Path, *, max_bytes: int, code: str) -> Path:
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    current = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError as exc:
            raise EvaluationConfigurationError(code) from exc
        if stat.S_ISLNK(mode):
            raise EvaluationConfigurationError(code)
    info = candidate.stat()
    if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= max_bytes:
        raise EvaluationConfigurationError(code)
    return candidate


def _bounded_text(value: Any, *, code: str, maximum: int = 131_072) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or "\x00" in value:
        raise EvaluationConfigurationError(code)
    return value


def _safe_id(value: Any, *, code: str) -> str:
    if not isinstance(value, str) or not SAFE_ID_RE.fullmatch(value):
        raise EvaluationConfigurationError(code)
    return value


def _finite_number(value: Any, *, code: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationConfigurationError(code)
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise EvaluationConfigurationError(code)
    return number


def _expected_strings(value: Any, *, code: str, safe_ids: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 100:
        raise EvaluationConfigurationError(code)
    resolved: list[str] = []
    for item in value:
        if safe_ids:
            text = _safe_id(item, code=code)
        else:
            text = _bounded_text(item, code=code, maximum=512)
            if any(ord(character) < 32 for character in text):
                raise EvaluationConfigurationError(code)
        if text in resolved:
            raise EvaluationConfigurationError(code)
        resolved.append(text)
    return tuple(resolved)


def _parse_expectations(payload: Any) -> CaseExpectations:
    if not isinstance(payload, dict) or set(payload) != EXPECTATION_FIELDS:
        raise EvaluationConfigurationError("dataset_expectations_schema_invalid")
    raw_numeric = payload["numeric"]
    if not isinstance(raw_numeric, list) or len(raw_numeric) > 100:
        raise EvaluationConfigurationError("dataset_numeric_invalid")
    numeric: list[NumericExpectation] = []
    numeric_ids: set[str] = set()
    for raw in raw_numeric:
        if not isinstance(raw, dict) or set(raw) != NUMERIC_FIELDS:
            raise EvaluationConfigurationError("dataset_numeric_invalid")
        expectation_id = _safe_id(raw["expectation_id"], code="dataset_numeric_id_invalid")
        if expectation_id in numeric_ids:
            raise EvaluationConfigurationError("dataset_numeric_id_duplicate")
        numeric_ids.add(expectation_id)
        value = _finite_number(
            raw["value"],
            code="dataset_numeric_value_invalid",
            minimum=-1e308,
            maximum=1e308,
        )
        tolerance = _finite_number(
            raw["absolute_tolerance"],
            code="dataset_numeric_tolerance_invalid",
            minimum=0,
            maximum=1e308,
        )
        numeric.append(NumericExpectation(expectation_id, value, tolerance))
    abstention_required = payload["abstention_required"]
    fallback_expected = payload["fallback_expected"]
    policy_denial_expected = payload["policy_denial_expected"]
    if not isinstance(abstention_required, bool):
        raise EvaluationConfigurationError("dataset_abstention_required_invalid")
    if fallback_expected is not None and not isinstance(fallback_expected, bool):
        raise EvaluationConfigurationError("dataset_fallback_expected_invalid")
    if not isinstance(policy_denial_expected, bool):
        raise EvaluationConfigurationError("dataset_policy_denial_expected_invalid")
    markers = _expected_strings(payload["abstention_markers"], code="dataset_abstention_markers_invalid")
    if abstention_required and not markers:
        raise EvaluationConfigurationError("dataset_abstention_markers_required")
    return CaseExpectations(
        numeric=tuple(numeric),
        citations=_expected_strings(payload["citations"], code="dataset_citations_invalid"),
        evidence_ids=_expected_strings(payload["evidence_ids"], code="dataset_evidence_ids_invalid"),
        required_sections=_expected_strings(
            payload["required_sections"],
            code="dataset_required_sections_invalid",
        ),
        abstention_required=abstention_required,
        abstention_markers=markers,
        required_tools=_expected_strings(
            payload["required_tools"],
            code="dataset_required_tools_invalid",
            safe_ids=True,
        ),
        fallback_expected=fallback_expected,
        policy_denial_expected=policy_denial_expected,
    )


def parse_dataset(payload: Any, *, sha256: str) -> EvaluationDataset:
    if not isinstance(payload, dict) or set(payload) != DATASET_FIELDS:
        raise EvaluationConfigurationError("dataset_schema_invalid")
    if payload["schema_version"] != DATASET_SCHEMA_VERSION:
        raise EvaluationConfigurationError("dataset_schema_version_invalid")
    if payload["profile"] != "siq_analysis":
        raise EvaluationConfigurationError("dataset_profile_invalid")
    model = payload["model"]
    if (
        not isinstance(model, str)
        or not SAFE_RUNTIME_LABEL_RE.fullmatch(model)
        or "://" in model
        or model.lower().startswith("bearer")
    ):
        raise EvaluationConfigurationError("dataset_model_invalid")
    temperature = _finite_number(
        payload["temperature"],
        code="dataset_temperature_invalid",
        minimum=0,
        maximum=2,
    )
    instructions = _bounded_text(payload["instructions"], code="dataset_instructions_invalid")
    repetitions = payload["repetitions"]
    if (
        isinstance(repetitions, bool)
        or not isinstance(repetitions, int)
        or not MIN_EVALUATION_REPETITIONS <= repetitions <= 10
    ):
        raise EvaluationConfigurationError("dataset_repetitions_invalid")
    run_timeout = _finite_number(
        payload["run_timeout_seconds"],
        code="dataset_timeout_invalid",
        minimum=0.05,
        maximum=3_600,
    )
    raw_cases = payload["cases"]
    if not isinstance(raw_cases, list) or not MIN_EVALUATION_CASES <= len(raw_cases) <= 500:
        raise EvaluationConfigurationError("dataset_cases_invalid")
    cases: list[EvaluationCase] = []
    case_ids: set[str] = set()
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict) or set(raw_case) != CASE_FIELDS:
            raise EvaluationConfigurationError("dataset_case_schema_invalid")
        case_id = _safe_id(raw_case["case_id"], code="dataset_case_id_invalid")
        if case_id in case_ids:
            raise EvaluationConfigurationError("dataset_case_id_duplicate")
        case_ids.add(case_id)
        input_text = _bounded_text(raw_case["input"], code="dataset_case_input_invalid")
        raw_history = raw_case["history"]
        if not isinstance(raw_history, list) or len(raw_history) > 100:
            raise EvaluationConfigurationError("dataset_history_invalid")
        history: list[dict[str, str]] = []
        for message in raw_history:
            if not isinstance(message, dict) or set(message) != MESSAGE_FIELDS:
                raise EvaluationConfigurationError("dataset_history_message_invalid")
            if message["role"] not in {"assistant", "system", "user"}:
                raise EvaluationConfigurationError("dataset_history_role_invalid")
            history.append(
                {
                    "role": message["role"],
                    "content": _bounded_text(
                        message["content"],
                        code="dataset_history_content_invalid",
                    ),
                }
            )
        cases.append(
            EvaluationCase(
                case_id=case_id,
                input_text=input_text,
                history=tuple(history),
                expectations=_parse_expectations(raw_case["expectations"]),
                case_hash=_sha256(_canonical_json(raw_case)),
            )
        )
    return EvaluationDataset(
        profile="siq_analysis",
        model=model,
        temperature=temperature,
        instructions=instructions,
        repetitions=repetitions,
        run_timeout_seconds=run_timeout,
        cases=tuple(cases),
        sha256=sha256,
    )


def load_dataset(path: Path) -> EvaluationDataset:
    source = _safe_regular_file(path, max_bytes=MAX_DATASET_BYTES, code="dataset_file_invalid")
    content = source.read_bytes()
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvaluationConfigurationError("dataset_json_invalid") from exc
    return parse_dataset(payload, sha256=_sha256(content))


def normalize_runs_url(value: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, ValueError) as exc:
        raise EvaluationConfigurationError("runs_url_invalid") from exc
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or port is None
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/v1/runs"
    ):
        raise EvaluationConfigurationError("runs_url_invalid")
    hostname = parsed.hostname.lower()
    if hostname != "localhost":
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError as exc:
            raise EvaluationConfigurationError("runs_url_not_loopback") from exc
        if not address.is_loopback:
            raise EvaluationConfigurationError("runs_url_not_loopback")
    rendered_host = f"[{hostname}]" if ":" in hostname else hostname
    return f"http://{rendered_host}:{port}/v1/runs"


def load_api_key(path: Path) -> str:
    source = _safe_regular_file(path, max_bytes=4_096, code="api_key_file_invalid")
    if source.stat().st_mode & 0o077:
        raise EvaluationConfigurationError("api_key_file_permissions_invalid")
    try:
        key = source.read_text(encoding="utf-8").strip()
    except UnicodeDecodeError as exc:
        raise EvaluationConfigurationError("api_key_file_invalid") from exc
    if (
        not 16 <= len(key) <= 1_024
        or any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in key)
        or key.lower().startswith("bearer")
    ):
        raise EvaluationConfigurationError("api_key_file_invalid")
    return key


def _safe_error_code(value: Any, default: str) -> str:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if ERROR_CODE_RE.fullmatch(normalized):
            return normalized
    return default


def _read_json_response(response: Any, *, expected_status: int, code: str) -> dict[str, Any]:
    if response.status != expected_status:
        raise RunContractError(f"{code}_http_{response.status}")
    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise RunContractError(f"{code}_content_type")
    content = response.read(MAX_JSON_RESPONSE_BYTES + 1)
    if len(content) > MAX_JSON_RESPONSE_BYTES:
        raise RunContractError(f"{code}_response_too_large")
    try:
        payload = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RunContractError(f"{code}_json_invalid") from exc
    if not isinstance(payload, dict):
        raise RunContractError(f"{code}_schema_invalid")
    return payload


class RunsClient:
    def __init__(
        self,
        *,
        runs_url: str,
        api_key: str,
        opener: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.runs_url = normalize_runs_url(runs_url)
        self._api_key = api_key
        self._opener = (
            opener
            or urllib.request.build_opener(
                urllib.request.ProxyHandler({}),
                NoRedirectHandler(),
            ).open
        )
        self._clock = clock

    def _request(
        self,
        url: str,
        *,
        method: str,
        payload: Mapping[str, Any] | None = None,
        timeout: float,
    ) -> Any:
        body = _canonical_json(payload) if payload is not None else None
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            return self._opener(request, timeout=max(0.05, timeout))
        except urllib.error.HTTPError as exc:
            raise RunContractError(f"http_{method.lower()}_{exc.code}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise RunTimedOut("http_timeout") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise RunTimedOut("http_timeout") from exc
            raise RunContractError("http_unavailable") from exc
        except OSError as exc:
            raise RunContractError("http_unavailable") from exc

    def _create(self, payload: Mapping[str, Any], *, timeout: float) -> str:
        with self._request(
            self.runs_url,
            method="POST",
            payload=payload,
            timeout=timeout,
        ) as response:
            body = _read_json_response(response, expected_status=202, code="create")
        if set(body) != {"run_id", "status"} or body.get("status") != "started":
            raise RunContractError("create_schema_invalid")
        run_id = body.get("run_id")
        if not isinstance(run_id, str) or not RUN_ID_RE.fullmatch(run_id):
            raise RunContractError("create_run_id_invalid")
        return run_id

    def _stop(self, run_id: str) -> tuple[bool, str]:
        try:
            with self._request(
                f"{self.runs_url}/{run_id}/stop",
                method="POST",
                timeout=5,
            ) as response:
                body = _read_json_response(response, expected_status=200, code="stop")
            if body != {"run_id": run_id, "status": "stopping"}:
                return False, "stop_schema_invalid"
            return True, ""
        except (RunContractError, RunTimedOut) as exc:
            return False, exc.code

    @staticmethod
    def _set_response_read_timeout(response: Any, timeout: float) -> None:
        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        sock = getattr(raw, "_sock", None)
        setter = getattr(sock, "settimeout", None)
        if callable(setter):
            setter(max(0.05, timeout))

    def _runtime_projection(
        self,
        value: Any,
    ) -> tuple[bool | None, str | None, str | None, str | None, str | None, str | None]:
        if not isinstance(value, dict) or value.get("schema_version") != "hermes.run_runtime.v1":
            return None, None, None, None, None, None
        fallback = value.get("fallback")
        configured = value.get("configured")
        effective = value.get("effective")
        if not isinstance(fallback, dict) or not isinstance(configured, dict) or not isinstance(effective, dict):
            return None, None, None, None, None, None
        activated = fallback.get("activated")
        if activated is not None and not isinstance(activated, bool):
            activated = None

        def label(item: Any) -> str | None:
            if isinstance(item, str) and SAFE_RUNTIME_LABEL_RE.fullmatch(item) and "://" not in item:
                return item
            return None

        return (
            activated,
            label(value.get("requested_model")),
            label(configured.get("provider")),
            label(configured.get("model")),
            label(effective.get("provider")),
            label(effective.get("model")),
        )

    def execute(self, payload: Mapping[str, Any], *, timeout_seconds: float) -> RunObservation:
        started = self._clock()
        try:
            run_id = self._create(payload, timeout=min(30.0, timeout_seconds))
        except RunTimedOut as exc:
            return RunObservation(
                status="timed_out",
                error_code=exc.code,
                total_duration_ms=round((self._clock() - started) * 1_000, 3),
            )
        except RunContractError as exc:
            return RunObservation(
                status="create_failed",
                error_code=exc.code,
                total_duration_ms=round((self._clock() - started) * 1_000, 3),
            )

        observation = RunObservation(status="sse_failed", run_id=run_id, create_contract_ok=True)
        deadline = started + timeout_seconds
        deltas: list[str] = []
        output_bytes = 0
        event_counts: Counter[str] = Counter()
        successful_tools: set[str] = set()
        failed_tools: set[str] = set()
        tool_attempt_counts: Counter[str] = Counter()
        tool_success_counts: Counter[str] = Counter()
        tool_failure_counts: Counter[str] = Counter()
        terminal_seen = False
        try:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise RunTimedOut("run_timeout")
            with self._request(
                f"{self.runs_url}/{run_id}/events",
                method="GET",
                timeout=remaining,
            ) as response:
                if response.status != 200:
                    raise RunContractError(f"sse_http_{response.status}")
                media_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if media_type != "text/event-stream":
                    raise RunContractError("sse_content_type")
                data_lines: list[str] = []
                data_bytes = 0
                while True:
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise RunTimedOut("run_timeout")
                    self._set_response_read_timeout(response, remaining)
                    raw_line = response.readline(MAX_SSE_EVENT_BYTES + 1)
                    if not raw_line:
                        break
                    if self._clock() >= deadline:
                        raise RunTimedOut("run_timeout")
                    if len(raw_line) > MAX_SSE_EVENT_BYTES:
                        raise RunContractError("sse_line_too_large")
                    try:
                        line = raw_line.decode("utf-8").rstrip("\r\n")
                    except UnicodeDecodeError as exc:
                        raise RunContractError("sse_utf8_invalid") from exc
                    if line.startswith("data:"):
                        data = line[5:].lstrip()
                        data_bytes += len(data.encode("utf-8"))
                        if data_bytes > MAX_SSE_EVENT_BYTES:
                            raise RunContractError("sse_event_too_large")
                        data_lines.append(data)
                        continue
                    if line or not data_lines:
                        continue
                    try:
                        event = json.loads(
                            "\n".join(data_lines),
                            object_pairs_hook=_reject_duplicate_keys,
                        )
                    except (json.JSONDecodeError, ValueError) as exc:
                        raise RunContractError("sse_json_invalid") from exc
                    data_lines = []
                    data_bytes = 0
                    if not isinstance(event, dict) or event.get("run_id") != run_id:
                        raise RunContractError("sse_event_envelope_invalid")
                    event_type = str(event.get("event") or "")
                    if not SAFE_ID_RE.fullmatch(event_type):
                        raise RunContractError("sse_event_type_invalid")
                    event_counts[event_type] += 1
                    if sum(event_counts.values()) > MAX_SSE_EVENTS:
                        raise RunContractError("sse_event_count_exceeded")
                    if event_type == "message.delta":
                        delta = event.get("delta")
                        if not isinstance(delta, str):
                            raise RunContractError("sse_delta_invalid")
                        if delta and observation.ttft_ms is None:
                            observation.ttft_ms = round((self._clock() - started) * 1_000, 3)
                        output_bytes += len(delta.encode("utf-8"))
                        if output_bytes > MAX_OUTPUT_BYTES:
                            raise RunContractError("run_output_too_large")
                        deltas.append(delta)
                    elif event_type == "tool.completed":
                        tool = event.get("tool")
                        if not isinstance(tool, str) or not SAFE_ID_RE.fullmatch(tool):
                            raise RunContractError("sse_tool_invalid")
                        # Keep final completion state separate from the audit-only
                        # set of tools that failed at least once during the run.
                        tool_attempt_counts[tool] += 1
                        if event.get("error") is True:
                            tool_failure_counts[tool] += 1
                            failed_tools.add(tool)
                            successful_tools.discard(tool)
                        else:
                            tool_success_counts[tool] += 1
                            successful_tools.add(tool)
                    elif event_type == "policy.denied":
                        observation.policy_denied = True
                        observation.error_code = _safe_error_code(
                            event.get("error_code"),
                            "policy_denied",
                        )
                    if event_type not in TERMINAL_EVENTS:
                        continue
                    if terminal_seen:
                        raise RunContractError("sse_duplicate_terminal")
                    terminal_seen = True
                    runtime = event.get("runtime")
                    (
                        observation.fallback_activated,
                        observation.requested_model,
                        observation.configured_provider,
                        observation.configured_model,
                        observation.effective_provider,
                        observation.effective_model,
                    ) = self._runtime_projection(runtime)
                    if event_type == "run.completed":
                        terminal_output = event.get("output", "")
                        if not isinstance(terminal_output, str):
                            terminal_output = json.dumps(terminal_output, ensure_ascii=False, sort_keys=True)
                        output_bytes = len(terminal_output.encode("utf-8"))
                        if output_bytes > MAX_OUTPUT_BYTES:
                            raise RunContractError("run_output_too_large")
                        observation.output = terminal_output or "".join(deltas)
                        observation.status = "completed"
                    elif event_type == "run.cancelled":
                        observation.output = "".join(deltas)
                        observation.status = "cancelled"
                        observation.error_code = "run_cancelled"
                    else:
                        observation.output = "".join(deltas)
                        error = event.get("error")
                        code = error.get("code") if isinstance(error, dict) else None
                        observation.error_code = _safe_error_code(code, "run_failed")
                        observation.policy_denied = observation.policy_denied or bool(
                            POLICY_ERROR_RE.search(observation.error_code)
                        )
                        observation.status = "failed"
                    observation.sse_contract_ok = True
                    observation.terminal_contract_ok = True
                    break
                if data_lines and not terminal_seen:
                    raise RunContractError("sse_unterminated_event")
            if not terminal_seen:
                raise RunContractError("sse_protocol_eof")
        except (RunTimedOut, TimeoutError, socket.timeout) as exc:
            observation.status = "timed_out"
            observation.error_code = exc.code if isinstance(exc, RunTimedOut) else "run_timeout"
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                observation.status = "timed_out"
                observation.error_code = "run_timeout"
            else:
                observation.status = "sse_failed"
                observation.error_code = "sse_unavailable"
        except (RunContractError, OSError) as exc:
            observation.status = "sse_failed"
            observation.error_code = exc.code if isinstance(exc, RunContractError) else "sse_unavailable"

        observation.event_counts = dict(sorted(event_counts.items()))
        observation.successful_tools = successful_tools
        observation.failed_tools = failed_tools
        observation.tool_attempt_counts = dict(sorted(tool_attempt_counts.items()))
        observation.tool_success_counts = dict(sorted(tool_success_counts.items()))
        observation.tool_failure_counts = dict(sorted(tool_failure_counts.items()))
        observation.total_duration_ms = round((self._clock() - started) * 1_000, 3)
        if not observation.terminal_contract_ok:
            observation.stop_attempted = True
            stop_ok, stop_error = self._stop(run_id)
            observation.stop_contract_ok = stop_ok
            if not stop_ok and not observation.error_code:
                observation.error_code = stop_error
        return observation


def build_run_payload(
    dataset: EvaluationDataset,
    case: EvaluationCase,
    *,
    evaluation_id: str,
    repetition: int,
) -> dict[str, Any]:
    return {
        "model": dataset.model,
        "temperature": dataset.temperature,
        "instructions": dataset.instructions,
        "input": case.input_text,
        "conversation_history": [dict(item) for item in case.history],
        "session_id": f"ab-{evaluation_id}-{case.case_id}-{repetition}",
    }


def interleaved_schedule(dataset: EvaluationDataset) -> list[tuple[int, str, EvaluationCase]]:
    schedule: list[tuple[int, str, EvaluationCase]] = []
    for repetition in range(dataset.repetitions):
        for case_index, case in enumerate(dataset.cases):
            arms = ("host", "openshell") if (repetition + case_index) % 2 == 0 else ("openshell", "host")
            schedule.extend((repetition, arm, case) for arm in arms)
    return schedule


def _numeric_values(output: str) -> list[float]:
    values: list[float] = []
    for match in NUMBER_RE.finditer(output):
        raw = match.group(0).replace(",", "").removesuffix("%")
        try:
            value = float(raw)
        except ValueError:
            continue
        if math.isfinite(value):
            values.append(value)
    return values


def score_run(case: EvaluationCase, observation: RunObservation) -> dict[str, Any]:
    output_casefold = observation.output.casefold()
    numbers = _numeric_values(observation.output)
    numeric_matched = sum(
        any(abs(actual - expected.value) <= expected.absolute_tolerance for actual in numbers)
        for expected in case.expectations.numeric
    )
    citation_matched = sum(item.casefold() in output_casefold for item in case.expectations.citations)
    evidence_matched = sum(item.casefold() in output_casefold for item in case.expectations.evidence_ids)
    headings = {
        " ".join(match.group(1).strip().casefold().split()) for match in HEADING_RE.finditer(observation.output)
    }
    required_sections_matched = sum(
        " ".join(item.casefold().split()) in headings for item in case.expectations.required_sections
    )
    abstention_matched = bool(
        observation.status == "completed"
        and case.expectations.abstention_required
        and any(marker.casefold() in output_casefold for marker in case.expectations.abstention_markers)
    )
    tools_matched = sum(tool in (observation.successful_tools or set()) for tool in case.expectations.required_tools)
    fallback_denominator = int(case.expectations.fallback_expected is True)
    fallback_matched = int(
        fallback_denominator == 1 and observation.status == "completed" and observation.fallback_activated is True
    )
    fallback_telemetry_expected = int(case.expectations.fallback_expected is not None)
    fallback_telemetry_observed = int(fallback_telemetry_expected == 1 and observation.fallback_activated is not None)
    unexpected_fallback = bool(case.expectations.fallback_expected is False and observation.fallback_activated is True)
    policy_false_positive = bool(not case.expectations.policy_denial_expected and observation.policy_denied)
    business_expectations_match = all(
        matched == expected
        for matched, expected in (
            (numeric_matched, len(case.expectations.numeric)),
            (citation_matched, len(case.expectations.citations)),
            (evidence_matched, len(case.expectations.evidence_ids)),
            (required_sections_matched, len(case.expectations.required_sections)),
            (int(abstention_matched), int(case.expectations.abstention_required)),
        )
    )
    fallback_matches = (
        case.expectations.fallback_expected is None
        or observation.fallback_activated is case.expectations.fallback_expected
    )
    task_success = (
        observation.policy_denied
        if case.expectations.policy_denial_expected
        else (
            observation.status == "completed"
            and observation.create_contract_ok
            and observation.sse_contract_ok
            and observation.terminal_contract_ok
            and not observation.policy_denied
            and not unexpected_fallback
            and business_expectations_match
            and fallback_matches
        )
    )
    return {
        "task_success": bool(task_success),
        "numeric": {"matched": numeric_matched, "expected": len(case.expectations.numeric)},
        "citations": {"matched": citation_matched, "expected": len(case.expectations.citations)},
        "evidence": {"matched": evidence_matched, "expected": len(case.expectations.evidence_ids)},
        "required_sections": {
            "matched": required_sections_matched,
            "expected": len(case.expectations.required_sections),
        },
        "hallucination_block": {
            "matched": int(abstention_matched),
            "expected": int(case.expectations.abstention_required),
        },
        "tools": {"matched": tools_matched, "expected": len(case.expectations.required_tools)},
        "fallback": {"matched": fallback_matched, "expected": fallback_denominator},
        "fallback_telemetry": {
            "matched": fallback_telemetry_observed,
            "expected": fallback_telemetry_expected,
        },
        "unexpected_fallback": unexpected_fallback,
        "timeout": observation.status == "timed_out",
        "policy_false_positive": policy_false_positive,
        "policy_false_positive_eligible": not case.expectations.policy_denial_expected,
    }


def _observation_record(
    *,
    sequence: int,
    arm: str,
    repetition: int,
    case: EvaluationCase,
    payload_sha256: str,
    observation: RunObservation,
    scores: Mapping[str, Any],
) -> dict[str, Any]:
    tool_names = set(observation.tool_attempt_counts or {})
    tool_outcomes = {
        tool: {
            "attempts": int((observation.tool_attempt_counts or {}).get(tool, 0)),
            "successes": int((observation.tool_success_counts or {}).get(tool, 0)),
            "failures": int((observation.tool_failure_counts or {}).get(tool, 0)),
            "final_status": "success" if tool in (observation.successful_tools or set()) else "failure",
        }
        for tool in sorted(tool_names)
    }
    runtime = {
        "fallback_activated": observation.fallback_activated,
        "requested_model": observation.requested_model,
        "configured_provider": observation.configured_provider,
        "configured_model": observation.configured_model,
        "effective_provider": observation.effective_provider,
        "effective_model": observation.effective_model,
    }
    return {
        "sequence": sequence,
        "arm": arm,
        "repetition": repetition,
        "case_id": case.case_id,
        "case_hash": case.case_hash,
        "payload_sha256": payload_sha256,
        "run_id_sha256": _sha256(observation.run_id) if observation.run_id else None,
        "status": observation.status,
        "error_code": observation.error_code,
        "contracts": {
            "create": observation.create_contract_ok,
            "sse": observation.sse_contract_ok,
            "terminal": observation.terminal_contract_ok,
            "stop_attempted": observation.stop_attempted,
            "stop": observation.stop_contract_ok,
        },
        "event_counts": dict(sorted((observation.event_counts or {}).items())),
        "successful_tools": sorted(observation.successful_tools or set()),
        "failed_tools": sorted(observation.failed_tools or set()),
        "tool_outcomes": tool_outcomes,
        "runtime": runtime,
        "policy_denied": observation.policy_denied,
        "ttft_ms": observation.ttft_ms,
        "total_duration_ms": observation.total_duration_ms,
        "output_sha256": _sha256(observation.output),
        "output_bytes": len(observation.output.encode("utf-8")),
        "scores": dict(scores),
    }


def percentile(values: Iterable[float], quantile: float) -> float | None:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    result = ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
    return round(result, 3)


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def summarize_arm(
    records: Iterable[Mapping[str, Any]],
    *,
    expected_primary_provider: str,
    expected_primary_model: str,
) -> dict[str, Any]:
    materialized = list(records)

    def score_pair(name: str) -> tuple[int, int]:
        return (
            sum(int(record["scores"][name]["matched"]) for record in materialized),
            sum(int(record["scores"][name]["expected"]) for record in materialized),
        )

    numeric = score_pair("numeric")
    citations = score_pair("citations")
    evidence = score_pair("evidence")
    sections = score_pair("required_sections")
    hallucination = score_pair("hallucination_block")
    tools = score_pair("tools")
    fallback = score_pair("fallback")
    fallback_telemetry = score_pair("fallback_telemetry")
    task_successes = sum(bool(record["scores"]["task_success"]) for record in materialized)
    timeouts = sum(bool(record["scores"]["timeout"]) for record in materialized)
    false_positive_eligible = sum(bool(record["scores"]["policy_false_positive_eligible"]) for record in materialized)
    false_positives = sum(bool(record["scores"]["policy_false_positive"]) for record in materialized)
    contract_failures = sum(
        not (record["contracts"]["create"] and record["contracts"]["sse"] and record["contracts"]["terminal"])
        for record in materialized
    )
    tool_outcomes = [
        outcome
        for record in materialized
        for outcome in record["tool_outcomes"].values()
    ]
    tool_attempt_count = sum(int(outcome["attempts"]) for outcome in tool_outcomes)
    tool_success_count = sum(int(outcome["successes"]) for outcome in tool_outcomes)
    tool_failure_count = sum(int(outcome["failures"]) for outcome in tool_outcomes)
    tool_retry_count = sum(max(int(outcome["attempts"]) - 1, 0) for outcome in tool_outcomes)
    failed_tool_state_count = sum(int(outcome["failures"]) > 0 for outcome in tool_outcomes)
    recovered_tool_state_count = sum(
        int(outcome["failures"]) > 0 and outcome["final_status"] == "success"
        for outcome in tool_outcomes
    )
    unrecovered_tool_state_count = failed_tool_state_count - recovered_tool_state_count
    ttft = [float(record["ttft_ms"]) for record in materialized if record["ttft_ms"] is not None]
    totals = [float(record["total_duration_ms"]) for record in materialized]
    configured_routes = Counter(
        (runtime.get("configured_provider"), runtime.get("configured_model"))
        for record in materialized
        if isinstance((runtime := record.get("runtime")), dict)
        and isinstance(runtime.get("configured_provider"), str)
        and isinstance(runtime.get("configured_model"), str)
    )
    effective_routes = Counter(
        (runtime.get("effective_provider"), runtime.get("effective_model"))
        for record in materialized
        if isinstance((runtime := record.get("runtime")), dict)
        and isinstance(runtime.get("effective_provider"), str)
        and isinstance(runtime.get("effective_model"), str)
    )
    telemetry_count = sum(
        isinstance((runtime := record.get("runtime")), dict)
        and isinstance(runtime.get("requested_model"), str)
        and isinstance(runtime.get("configured_provider"), str)
        and isinstance(runtime.get("configured_model"), str)
        and isinstance(runtime.get("effective_provider"), str)
        and isinstance(runtime.get("effective_model"), str)
        and isinstance(runtime.get("fallback_activated"), bool)
        for record in materialized
    )
    return {
        "execution_count": len(materialized),
        "task_success_rate": _rate(task_successes, len(materialized)),
        "answer_citation_rate": _rate(*citations),
        "numeric_accuracy": _rate(*numeric),
        "hallucination_block_rate": _rate(*hallucination),
        "evidence_coverage": _rate(*evidence),
        "tool_success_rate": _rate(*tools),
        "tool_error_rate": _rate(tool_failure_count, tool_attempt_count) or 0.0,
        "tool_retry_rate": _rate(tool_retry_count, tool_attempt_count) or 0.0,
        "tool_recovery_rate": (
            _rate(recovered_tool_state_count, failed_tool_state_count) if failed_tool_state_count else 1.0
        ),
        "tool_unrecovered_failure_rate": _rate(unrecovered_tool_state_count, failed_tool_state_count) or 0.0,
        "fallback_success_rate": _rate(*fallback),
        "fallback_expected_execution_count": fallback[1],
        "fallback_telemetry_coverage": _rate(*fallback_telemetry),
        "fallback_telemetry_expected_count": fallback_telemetry[1],
        "report_completeness": _rate(*sections),
        "timeout_rate": _rate(timeouts, len(materialized)),
        "policy_false_positive_rate": _rate(false_positives, false_positive_eligible),
        "sample_counts": {
            "answer_citation_rate": citations[1],
            "numeric_accuracy": numeric[1],
            "hallucination_block_rate": hallucination[1],
            "evidence_coverage": evidence[1],
            "tool_success_rate": tools[1],
            "report_completeness": sections[1],
            "policy_false_positive_rate": false_positive_eligible,
        },
        "contract_failure_count": contract_failures,
        "unexpected_fallback_count": sum(bool(record["scores"]["unexpected_fallback"]) for record in materialized),
        "tool_runtime": {
            "attempt_count": tool_attempt_count,
            "success_count": tool_success_count,
            "failure_count": tool_failure_count,
            "retry_count": tool_retry_count,
            "failed_tool_state_count": failed_tool_state_count,
            "recovered_tool_state_count": recovered_tool_state_count,
            "unrecovered_tool_state_count": unrecovered_tool_state_count,
        },
        "runtime_telemetry": {
            "expected_primary_provider": expected_primary_provider,
            "expected_primary_model": expected_primary_model,
            "telemetry_count": telemetry_count,
            "requested_model_match_count": sum(
                isinstance(record.get("runtime"), dict)
                and record["runtime"].get("requested_model") == expected_primary_model
                for record in materialized
            ),
            "configured_route_match_count": sum(
                isinstance(record.get("runtime"), dict)
                and record["runtime"].get("configured_provider") == expected_primary_provider
                and record["runtime"].get("configured_model") == expected_primary_model
                for record in materialized
            ),
            "effective_route_match_count": sum(
                isinstance(record.get("runtime"), dict)
                and record["runtime"].get("effective_provider") == expected_primary_provider
                and record["runtime"].get("effective_model") == expected_primary_model
                for record in materialized
            ),
            "fallback_inactive_count": sum(
                isinstance(record.get("runtime"), dict)
                and record["runtime"].get("fallback_activated") is False
                for record in materialized
            ),
            "configured_routes": [
                {"provider": provider, "model": model, "count": count}
                for (provider, model), count in sorted(configured_routes.items())
            ],
            "effective_routes": [
                {"provider": provider, "model": model, "count": count}
                for (provider, model), count in sorted(effective_routes.items())
            ],
        },
        "latency_ms": {
            "ttft_sample_count": len(ttft),
            "ttft_p50": percentile(ttft, 0.50),
            "ttft_p95": percentile(ttft, 0.95),
            "total_sample_count": len(totals),
            "total_p50": percentile(totals, 0.50),
            "total_p95": percentile(totals, 0.95),
        },
    }


def _comparison(host: Mapping[str, Any], openshell: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    metric_names = (
        "task_success_rate",
        "answer_citation_rate",
        "numeric_accuracy",
        "hallucination_block_rate",
        "evidence_coverage",
        "tool_success_rate",
        "tool_error_rate",
        "tool_retry_rate",
        "tool_recovery_rate",
        "tool_unrecovered_failure_rate",
        "fallback_success_rate",
        "fallback_telemetry_coverage",
        "report_completeness",
        "timeout_rate",
        "policy_false_positive_rate",
    )
    deltas: dict[str, float | None] = {}
    reasons: list[str] = []
    for metric in metric_names:
        left = host.get(metric)
        right = openshell.get(metric)
        deltas[metric] = round(float(right) - float(left), 6) if left is not None and right is not None else None
    if host["contract_failure_count"]:
        reasons.append("host_baseline_contract_failure")
    if openshell["contract_failure_count"]:
        reasons.append("openshell_contract_failure")
    if openshell["unexpected_fallback_count"]:
        reasons.append("openshell_unexpected_fallback")
    if host["fallback_telemetry_expected_count"] and host["fallback_telemetry_coverage"] != 1:
        reasons.append("host_fallback_telemetry_incomplete")
    if openshell["fallback_telemetry_expected_count"] and openshell["fallback_telemetry_coverage"] != 1:
        reasons.append("openshell_fallback_telemetry_incomplete")
    if host["fallback_expected_execution_count"] and host["fallback_success_rate"] != 1:
        reasons.append("host_fallback_validation_failed")
    if openshell["fallback_expected_execution_count"] and openshell["fallback_success_rate"] != 1:
        reasons.append("openshell_fallback_validation_failed")
    if host["task_success_rate"] is not None and openshell["task_success_rate"] < host["task_success_rate"]:
        reasons.append("task_success_regression")
    for metric in (
        "answer_citation_rate",
        "numeric_accuracy",
        "hallucination_block_rate",
        "evidence_coverage",
        "tool_success_rate",
        "fallback_success_rate",
        "fallback_telemetry_coverage",
        "report_completeness",
    ):
        if host[metric] is not None and openshell[metric] is not None and openshell[metric] < host[metric]:
            reasons.append(f"{metric}_regression")
    host_p95 = host["latency_ms"]["total_p95"]
    openshell_p95 = openshell["latency_ms"]["total_p95"]
    total_p95_ratio = (
        round(float(openshell_p95) / float(host_p95), 6)
        if host_p95 not in {None, 0} and openshell_p95 is not None
        else None
    )
    if total_p95_ratio is not None and total_p95_ratio > 1.10:
        reasons.append("total_p95_regression")
    false_positive_rate = openshell["policy_false_positive_rate"]
    if host["policy_false_positive_rate"] is None:
        reasons.append("host_policy_false_positive_coverage_missing")
    if false_positive_rate is None:
        reasons.append("openshell_policy_false_positive_coverage_missing")
    elif false_positive_rate > 0:
        reasons.append("golden_policy_false_positive")
    if (
        host["timeout_rate"] is not None
        and openshell["timeout_rate"] is not None
        and openshell["timeout_rate"] > host["timeout_rate"]
    ):
        reasons.append("timeout_rate_regression")
    return {"metric_deltas": deltas, "total_p95_ratio": total_p95_ratio}, sorted(set(reasons))


def quality_comparison(
    host: Mapping[str, Any],
    openshell: Mapping[str, Any],
    *,
    case_count: int,
    repetitions: int,
    require_fallback: bool = True,
) -> tuple[dict[str, Any], list[str]]:
    """Apply the release quality gate, including evidence sufficiency checks."""

    comparison, reasons = _comparison(host, openshell)
    if case_count < MIN_EVALUATION_CASES:
        reasons.append("evaluation_case_count_insufficient")
    if repetitions < MIN_EVALUATION_REPETITIONS:
        reasons.append("evaluation_repetitions_insufficient")

    for arm_name, arm in (("host", host), ("openshell", openshell)):
        execution_count = arm.get("execution_count")
        if not isinstance(execution_count, int) or isinstance(execution_count, bool) or execution_count < MIN_ARM_EXECUTIONS:
            reasons.append(f"{arm_name}_execution_sample_insufficient")

        sample_counts = arm.get("sample_counts")
        if not isinstance(sample_counts, dict) or any(
            not isinstance(sample_counts.get(metric), int)
            or isinstance(sample_counts.get(metric), bool)
            or sample_counts[metric] < MIN_PRIMARY_METRIC_SAMPLES
            for metric in (
                "answer_citation_rate",
                "numeric_accuracy",
                "hallucination_block_rate",
                "evidence_coverage",
                "tool_success_rate",
                "report_completeness",
            )
        ):
            reasons.append(f"{arm_name}_primary_metric_sample_insufficient")
        if (
            not isinstance(sample_counts, dict)
            or not isinstance(sample_counts.get("policy_false_positive_rate"), int)
            or isinstance(sample_counts.get("policy_false_positive_rate"), bool)
            or sample_counts["policy_false_positive_rate"] < MIN_POLICY_NORMAL_SAMPLES
        ):
            reasons.append(f"{arm_name}_policy_sample_insufficient")

        if require_fallback and (
            not isinstance(arm.get("fallback_expected_execution_count"), int)
            or isinstance(arm.get("fallback_expected_execution_count"), bool)
            or arm["fallback_expected_execution_count"] < MIN_FALLBACK_SAMPLES
            or not isinstance(arm.get("fallback_telemetry_expected_count"), int)
            or isinstance(arm.get("fallback_telemetry_expected_count"), bool)
            or arm["fallback_telemetry_expected_count"] < MIN_FALLBACK_SAMPLES
        ):
            reasons.append(f"{arm_name}_fallback_sample_insufficient")

        latency = arm.get("latency_ms")
        if (
            not isinstance(latency, dict)
            or not isinstance(latency.get("ttft_sample_count"), int)
            or isinstance(latency.get("ttft_sample_count"), bool)
            or latency["ttft_sample_count"] < MIN_LATENCY_SAMPLES
            or not isinstance(latency.get("total_sample_count"), int)
            or isinstance(latency.get("total_sample_count"), bool)
            or latency["total_sample_count"] < MIN_ARM_EXECUTIONS
        ):
            reasons.append(f"{arm_name}_latency_sample_insufficient")

        runtime = arm.get("runtime_telemetry")
        if not isinstance(runtime, dict):
            reasons.append(f"{arm_name}_runtime_telemetry_invalid")
        else:
            expected_count = execution_count if isinstance(execution_count, int) and not isinstance(execution_count, bool) else -1
            required_counts = ("telemetry_count", "requested_model_match_count", "configured_route_match_count")
            if any(runtime.get(field) != expected_count for field in required_counts):
                reasons.append(f"{arm_name}_primary_route_telemetry_incomplete")
            if not require_fallback and any(
                runtime.get(field) != expected_count
                for field in ("effective_route_match_count", "fallback_inactive_count")
            ):
                reasons.append(f"{arm_name}_primary_route_not_effective")

    for metric, floor in OPENSHELL_ABSOLUTE_QUALITY_FLOORS.items():
        value = openshell.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) < floor:
            reasons.append(f"openshell_{metric}_below_absolute_floor")
    for metric, ceiling in OPENSHELL_ABSOLUTE_RATE_CEILINGS.items():
        value = openshell.get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) > ceiling:
            reasons.append(f"openshell_{metric}_above_absolute_ceiling")

    host_runtime = host.get("runtime_telemetry")
    openshell_runtime = openshell.get("runtime_telemetry")
    if isinstance(host_runtime, dict) and isinstance(openshell_runtime, dict):
        if any(
            host_runtime.get(field) != openshell_runtime.get(field)
            for field in ("expected_primary_provider", "expected_primary_model", "configured_routes", "effective_routes")
        ):
            reasons.append("runtime_route_distribution_mismatch")

    return comparison, sorted(set(reasons))


def evaluate(
    dataset: EvaluationDataset,
    *,
    host_client: RunsClient,
    openshell_client: RunsClient,
    evaluation_id: str,
    prerequisites_path: str,
    prerequisites_sha256: str,
    expected_primary_provider: str,
    expected_primary_model: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not SAFE_ID_RE.fullmatch(evaluation_id):
        raise EvaluationConfigurationError("evaluation_id_invalid")
    if not SHA256_RE.fullmatch(prerequisites_sha256):
        raise EvaluationConfigurationError("prerequisites_sha256_invalid")
    expected_prerequisites_path = f"var/openshell/eval/{evaluation_id}/prerequisites.json"
    if prerequisites_path != expected_prerequisites_path:
        raise EvaluationConfigurationError("prerequisites_path_invalid")
    for label in (expected_primary_provider, expected_primary_model):
        if not isinstance(label, str) or not SAFE_RUNTIME_LABEL_RE.fullmatch(label) or "://" in label:
            raise EvaluationConfigurationError("expected_primary_route_invalid")
    clients = {"host": host_client, "openshell": openshell_client}
    raw_records: list[dict[str, Any]] = []
    for sequence, (repetition, arm, case) in enumerate(interleaved_schedule(dataset), start=1):
        payload = build_run_payload(
            dataset,
            case,
            evaluation_id=evaluation_id,
            repetition=repetition,
        )
        payload_sha256 = _sha256(_canonical_json(payload))
        observation = clients[arm].execute(copy.deepcopy(payload), timeout_seconds=dataset.run_timeout_seconds)
        scores = score_run(case, observation)
        raw_records.append(
            _observation_record(
                sequence=sequence,
                arm=arm,
                repetition=repetition,
                case=case,
                payload_sha256=payload_sha256,
                observation=observation,
                scores=scores,
            )
        )
    host_summary = summarize_arm(
        (record for record in raw_records if record["arm"] == "host"),
        expected_primary_provider=expected_primary_provider,
        expected_primary_model=expected_primary_model,
    )
    openshell_summary = summarize_arm(
        (record for record in raw_records if record["arm"] == "openshell"),
        expected_primary_provider=expected_primary_provider,
        expected_primary_model=expected_primary_model,
    )
    comparison, failure_reasons = quality_comparison(
        host_summary,
        openshell_summary,
        case_count=len(dataset.cases),
        repetitions=dataset.repetitions,
        require_fallback=any(case.expectations.fallback_expected is True for case in dataset.cases),
    )
    raw = {
        "schema_version": RAW_SCHEMA_VERSION,
        "evaluation_id": evaluation_id,
        "prerequisites_path": prerequisites_path,
        "prerequisites_sha256": prerequisites_sha256,
        "dataset_sha256": dataset.sha256,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "configuration": {
            "profile": dataset.profile,
            "model": dataset.model,
            "temperature": dataset.temperature,
            "repetitions": dataset.repetitions,
            "run_timeout_seconds": dataset.run_timeout_seconds,
            "interleaving": "alternating_case_and_repetition",
        },
        "cutover_performed": False,
        "results": raw_records,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "evaluation_id": evaluation_id,
        "prerequisites_path": prerequisites_path,
        "prerequisites_sha256": prerequisites_sha256,
        "dataset_sha256": dataset.sha256,
        "dataset_schema_version": DATASET_SCHEMA_VERSION,
        "profile": dataset.profile,
        "model": dataset.model,
        "temperature": dataset.temperature,
        "case_count": len(dataset.cases),
        "repetitions": dataset.repetitions,
        "execution_count": len(raw_records),
        "interleaving": "alternating_case_and_repetition",
        "arms": {"host": host_summary, "openshell": openshell_summary},
        "comparison": comparison,
        "quality_gate": {
            "passed": not failure_reasons,
            "failure_reasons": failure_reasons,
            "cutover_performed": False,
            "recommendation": "manual_review_only_no_automatic_cutover",
        },
        "sanitization": {
            "contains_api_keys": False,
            "contains_headers": False,
            "contains_prompt_or_input": False,
            "contains_raw_output": False,
            "t8_exporter_ready": True,
        },
    }
    return raw, summary


def _safe_eval_directory(project_root: Path, evaluation_id: str) -> Path:
    if not SAFE_ID_RE.fullmatch(evaluation_id):
        raise EvaluationConfigurationError("evaluation_id_invalid")
    try:
        root = project_root.expanduser().resolve(strict=True)
    except OSError as exc:
        raise EvaluationConfigurationError("project_root_invalid") from exc
    if not root.is_dir() or root.is_symlink():
        raise EvaluationConfigurationError("project_root_invalid")
    current = root
    for relative in (Path("var"), Path("openshell"), Path("eval")):
        current /= relative
        if current.exists():
            if current.is_symlink() or not current.is_dir():
                raise EvaluationConfigurationError("evaluation_root_invalid")
        else:
            current.mkdir(mode=0o700)
        if relative == Path("eval"):
            os.chmod(current, 0o700)
    output = current / evaluation_id
    if output.exists():
        if output.is_symlink() or not output.is_dir():
            raise EvaluationConfigurationError("evaluation_output_invalid")
        info = output.stat()
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise EvaluationConfigurationError("evaluation_output_invalid")
        for child in output.iterdir():
            if child.name not in ALLOWED_PREEXISTING_EVAL_FILES:
                raise EvaluationConfigurationError("evaluation_output_contains_unexpected_entry")
            child_info = child.lstat()
            if (
                stat.S_ISLNK(child_info.st_mode)
                or not stat.S_ISREG(child_info.st_mode)
                or child_info.st_uid != os.geteuid()
                or child_info.st_nlink != 1
                or stat.S_IMODE(child_info.st_mode) != 0o600
            ):
                raise EvaluationConfigurationError("evaluation_output_invalid")
    else:
        output.mkdir(mode=0o700)
    if (output / "raw-results.json").exists() or (output / "summary.json").exists():
        raise EvaluationConfigurationError("evaluation_output_exists")
    return output


def _exclusive_write(path: Path, payload: Mapping[str, Any]) -> None:
    content = (json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
    if any(term in content.lower() for term in FORBIDDEN_RAW_FIELDS):
        raise EvaluationConfigurationError("evaluation_artifact_forbidden_field")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    finally:
        os.close(descriptor)


def write_evaluation_artifacts(
    *,
    project_root: Path,
    evaluation_id: str,
    raw: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> tuple[Path, Path]:
    output = _safe_eval_directory(project_root, evaluation_id)
    raw_path = output / "raw-results.json"
    summary_path = output / "summary.json"
    created: list[Path] = []
    try:
        created.append(raw_path)
        _exclusive_write(raw_path, raw)
        created.append(summary_path)
        _exclusive_write(summary_path, summary)
        findings = check_sanitized_artifacts.scan_paths(created)
        if findings:
            raise EvaluationConfigurationError("evaluation_artifact_sanitization_failed")
    except Exception:
        for path in created:
            path.unlink(missing_ok=True)
        try:
            output.rmdir()
        except OSError:
            pass
        raise
    return raw_path, summary_path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--host-runs-url", required=True)
    parser.add_argument("--openshell-runs-url", required=True)
    parser.add_argument("--host-api-key-file", type=Path, required=True)
    parser.add_argument("--openshell-api-key-file", type=Path, required=True)
    parser.add_argument("--evaluation-id", required=True)
    parser.add_argument(
        "--prerequisites",
        type=Path,
        required=True,
        help="v3 GO report emitted by check_siq_analysis_ab_prerequisites.py",
    )
    parser.add_argument("--project-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--confirm-live-evaluation",
        action="store_true",
        help="required acknowledgement; this never changes SIQ runtime routing",
    )
    return parser


def _load_prerequisite_binding(
    path: Path,
    *,
    project_root: Path,
    evaluation_id: str,
    dataset_sha256: str,
    host_runs_url: str,
    openshell_runs_url: str,
    host_key_fingerprint: str,
    openshell_key_fingerprint: str,
) -> tuple[str, str, str, str]:
    from scripts.openshell import check_siq_analysis_ab_prerequisites as prerequisite_gate

    root = project_root.expanduser().resolve(strict=True)
    relative_path = Path("var/openshell/eval") / evaluation_id / "prerequisites.json"
    expected_path = root / relative_path
    candidate = path.expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved_candidate = candidate.resolve(strict=True)
    except OSError as exc:
        raise EvaluationConfigurationError("prerequisites_file_invalid") from exc
    if resolved_candidate != expected_path:
        raise EvaluationConfigurationError("prerequisites_path_invalid")
    try:
        report, digest = prerequisite_gate.validate_report_for_evaluation(
            path,
            evaluation_id=evaluation_id,
            dataset_sha256=dataset_sha256,
            host_runs_url=host_runs_url,
            openshell_runs_url=openshell_runs_url,
            host_key_fingerprint=host_key_fingerprint,
            openshell_key_fingerprint=openshell_key_fingerprint,
        )
    except prerequisite_gate.PrerequisiteError as exc:
        raise EvaluationConfigurationError(str(exc)) from exc
    provenance_path = expected_path.with_name("provenance.json")
    checked_provenance = _safe_regular_file(
        provenance_path,
        max_bytes=256 * 1024,
        code="provenance_file_invalid",
    )
    if stat.S_IMODE(checked_provenance.stat().st_mode) != 0o600:
        raise EvaluationConfigurationError("provenance_permissions_invalid")
    try:
        content = checked_provenance.read_bytes()
        provenance = json.loads(content, object_pairs_hook=_reject_duplicate_keys)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise EvaluationConfigurationError("provenance_file_invalid") from exc
    report_provenance = report.get("provenance")
    attestation = provenance.get("runtime_attestation") if isinstance(provenance, dict) else None
    if (
        not isinstance(report_provenance, dict)
        or report_provenance.get("sha256") != _sha256(content)
        or provenance.get("schema_version") != prerequisite_gate.PROVENANCE_SCHEMA_VERSION
        or provenance.get("evaluation_id") != evaluation_id
        or provenance.get("dataset_sha256") != dataset_sha256
        or not isinstance(attestation, dict)
    ):
        raise EvaluationConfigurationError("provenance_binding_invalid")
    primary_provider = attestation.get("primary_provider")
    primary_model = attestation.get("primary_model")
    if any(
        not isinstance(label, str) or not SAFE_RUNTIME_LABEL_RE.fullmatch(label) or "://" in label
        for label in (primary_provider, primary_model)
    ):
        raise EvaluationConfigurationError("provenance_primary_route_invalid")
    return relative_path.as_posix(), digest, primary_provider, primary_model


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not args.confirm_live_evaluation:
        print(json.dumps({"ok": False, "error_code": "live_evaluation_not_confirmed"}), file=sys.stderr)
        return 2
    try:
        dataset = load_dataset(args.dataset)
        host_url = normalize_runs_url(args.host_runs_url)
        openshell_url = normalize_runs_url(args.openshell_runs_url)
        if host_url == openshell_url:
            raise EvaluationConfigurationError("ab_endpoints_must_differ")
        host_key_path = _safe_regular_file(
            args.host_api_key_file,
            max_bytes=4_096,
            code="api_key_file_invalid",
        ).resolve(strict=True)
        openshell_key_path = _safe_regular_file(
            args.openshell_api_key_file,
            max_bytes=4_096,
            code="api_key_file_invalid",
        ).resolve(strict=True)
        if host_key_path == openshell_key_path:
            raise EvaluationConfigurationError("api_key_files_must_differ")
        host_key = load_api_key(host_key_path)
        openshell_key = load_api_key(openshell_key_path)
        if hmac.compare_digest(host_key, openshell_key):
            raise EvaluationConfigurationError("api_keys_must_differ")
        prerequisites_path, prerequisites_sha256, primary_provider, primary_model = _load_prerequisite_binding(
            args.prerequisites,
            project_root=args.project_root,
            evaluation_id=args.evaluation_id,
            dataset_sha256=dataset.sha256,
            host_runs_url=host_url,
            openshell_runs_url=openshell_url,
            host_key_fingerprint=_sha256(host_key),
            openshell_key_fingerprint=_sha256(openshell_key),
        )
        # Fail before constructing a RunsClient if the prepared private output
        # directory cannot later accept the exclusively written artifacts.
        _safe_eval_directory(args.project_root, args.evaluation_id)
        host_client = RunsClient(runs_url=host_url, api_key=host_key)
        openshell_client = RunsClient(
            runs_url=openshell_url,
            api_key=openshell_key,
        )
        raw, summary = evaluate(
            dataset,
            host_client=host_client,
            openshell_client=openshell_client,
            evaluation_id=args.evaluation_id,
            prerequisites_path=prerequisites_path,
            prerequisites_sha256=prerequisites_sha256,
            expected_primary_provider=primary_provider,
            expected_primary_model=primary_model,
        )
        write_evaluation_artifacts(
            project_root=args.project_root,
            evaluation_id=args.evaluation_id,
            raw=raw,
            summary=summary,
        )
        gate_passed = bool(summary["quality_gate"]["passed"])
        print(
            json.dumps(
                {
                    "ok": gate_passed,
                    "schema_version": SUMMARY_SCHEMA_VERSION,
                    "evaluation_id": args.evaluation_id,
                    "cutover_performed": False,
                },
                sort_keys=True,
            )
        )
        return 0 if gate_passed else 1
    except EvaluationConfigurationError as exc:
        print(
            json.dumps({"ok": False, "error_code": str(exc), "cutover_performed": False}, sort_keys=True),
            file=sys.stderr,
        )
        return 2
    except (OSError, ValueError):
        print(
            json.dumps(
                {"ok": False, "error_code": "evaluation_io_error", "cutover_performed": False},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
