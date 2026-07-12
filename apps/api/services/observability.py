"""Small observability helpers shared by API runtime code."""

from __future__ import annotations

import contextvars
import json
import logging
import re
import threading
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping
from typing import Any

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_LOG_FIELD = "request_id"
MAX_REQUEST_ID_LENGTH = 128
SENSITIVE_KEY_TERMS = ("authorization", "bearer", "cookie", "password", "secret", "token", "api_key", "key")
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:/#=-]+$")
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("siq_request_id", default="")
_START_TIME = time.time()
_METRICS_LOCK = threading.Lock()
_HTTP_REQUEST_TOTAL: Counter[tuple[str, str, str]] = Counter()
_HTTP_REQUEST_DURATION_MS: dict[tuple[str, str], dict[str, float]] = defaultdict(
    lambda: {"count": 0.0, "sum": 0.0, "max": 0.0}
)
_AGENT_FACT_SOURCE_TOTAL: Counter[str] = Counter()
_POSTGRES_FALLBACK_REASON_TOTAL: Counter[str] = Counter()
_ANSWER_GUARDRAIL_BLOCK_TOTAL: Counter[str] = Counter()
_ANSWER_CALCULATOR_RUN_TOTAL = 0
_ANSWER_CITATION_TOTAL = 0
_INGESTION_DURATION_SECONDS: dict[tuple[str, str, str], dict[str, float]] = defaultdict(
    lambda: {"count": 0.0, "sum": 0.0, "max": 0.0}
)
_INGESTION_FACT_COUNT: dict[tuple[str, str], float] = {}
_WIKI_POSTGRES_PARITY_WARNING_TOTAL: Counter[tuple[str, str]] = Counter()
_FRONTEND_PIPELINE_JOB_FAILURE_TOTAL: Counter[tuple[str, str, str]] = Counter()
_BACKGROUND_JOB_FINAL_STATE_TOTAL: Counter[tuple[str, str]] = Counter()
_BACKGROUND_JOB_PERSISTENCE_FAILURE_TOTAL: Counter[str] = Counter()
_BACKGROUND_JOB_DURATION_SECONDS: dict[tuple[str, str], dict[str, float]] = defaultdict(
    lambda: {"count": 0.0, "sum": 0.0, "max": 0.0}
)


def current_request_id() -> str:
    return _request_id_var.get()


def set_request_id(request_id: str) -> contextvars.Token[str]:
    return _request_id_var.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id_var.reset(token)


def normalize_request_id(value: Any | None) -> str:
    text = str(value or "").strip()
    if text and len(text) <= MAX_REQUEST_ID_LENGTH and _REQUEST_ID_RE.fullmatch(text):
        return text
    return uuid.uuid4().hex


def monotonic_ms(start: float) -> int:
    return max(0, int((time.perf_counter() - start) * 1000))


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(term in lowered for term in SENSITIVE_KEY_TERMS)


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "***REDACTED***" if _is_sensitive_key(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


def emit_json_log(logger: logging.Logger, event: str, **fields: Any) -> None:
    payload = {
        "event": event,
        REQUEST_ID_LOG_FIELD: fields.pop(REQUEST_ID_LOG_FIELD, current_request_id()),
        **redact_sensitive(fields),
    }
    logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _label_value(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:/#={}-]+", "_", str(value or "unknown").strip())[:160]
    return text or "unknown"


def _prom_label(value: Any) -> str:
    return _label_value(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def normalize_http_metric_path(path: str, route_template: str | None = None) -> str:
    """Return a bounded-cardinality route label.

    FastAPI's resolved route template is preferred. Middleware can observe an
    arbitrary unmatched path before routing, so that fallback is intentionally
    represented by one fixed label rather than user-controlled path segments.
    """
    candidate = str(route_template or path or "/").strip() or "/"
    if route_template:
        return _label_value(candidate)
    return "/__unmatched__"


def record_http_request(
    method: str,
    path: str,
    status_code: int,
    duration_ms: int | float,
    *,
    route_template: str | None = None,
) -> None:
    method_label = _label_value(method).upper()
    path_label = normalize_http_metric_path(path, route_template)
    status_label = str(int(status_code or 500))
    duration = max(0.0, float(duration_ms or 0))
    with _METRICS_LOCK:
        _HTTP_REQUEST_TOTAL[(method_label, path_label, status_label)] += 1
        bucket = _HTTP_REQUEST_DURATION_MS[(method_label, path_label)]
        bucket["count"] += 1
        bucket["sum"] += duration
        bucket["max"] = max(bucket["max"], duration)


def record_answer_audit_observation(record: Mapping[str, Any] | None) -> None:
    if not isinstance(record, Mapping):
        return

    source_types: list[str] = []
    for key in ("citations", "wiki_facts", "postgres_facts"):
        items = record.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, Mapping) and item.get("source_type"):
                source_types.append(str(item["source_type"]))

    fallback_reason = str(record.get("fallback_reason") or "").strip()
    guardrail = record.get("guardrail_result") if isinstance(record.get("guardrail_result"), Mapping) else {}
    blocked = bool(guardrail.get("blocked") is True or guardrail.get("allowed") is False)
    calculator_runs = record.get("calculator_runs") if isinstance(record.get("calculator_runs"), list) else []
    citations = record.get("citations") if isinstance(record.get("citations"), list) else []

    with _METRICS_LOCK:
        for source_type in source_types:
            _AGENT_FACT_SOURCE_TOTAL[_label_value(source_type)] += 1
        if fallback_reason:
            _POSTGRES_FALLBACK_REASON_TOTAL[_label_value(fallback_reason)] += 1
        _ANSWER_GUARDRAIL_BLOCK_TOTAL["true" if blocked else "false"] += 1
        global _ANSWER_CALCULATOR_RUN_TOTAL, _ANSWER_CITATION_TOTAL
        _ANSWER_CALCULATOR_RUN_TOTAL += len(calculator_runs)
        _ANSWER_CITATION_TOTAL += len(citations)


def record_ingestion_duration(
    *,
    market: str | None,
    stage: str,
    duration_seconds: int | float,
    status: str | None = None,
) -> None:
    key = (_label_value(market), _label_value(stage), _label_value(status or "unknown"))
    duration = max(0.0, float(duration_seconds or 0))
    with _METRICS_LOCK:
        bucket = _INGESTION_DURATION_SECONDS[key]
        bucket["count"] += 1
        bucket["sum"] += duration
        bucket["max"] = max(bucket["max"], duration)


def record_ingestion_fact_counts(*, market: str | None, counts: Mapping[str, Any] | None) -> None:
    if not isinstance(counts, Mapping):
        return
    market_label = _label_value(market)
    with _METRICS_LOCK:
        for kind in ("parse_runs", "facts", "tables", "chunks", "evidence"):
            value = counts.get(kind)
            try:
                _INGESTION_FACT_COUNT[(market_label, _label_value(kind))] = max(0.0, float(value or 0))
            except (TypeError, ValueError):
                continue


def record_wiki_postgres_parity_warning(*, market: str | None, diff_code: str | None, count: int | float = 1) -> None:
    increment = int(count or 0)
    if increment <= 0:
        return
    with _METRICS_LOCK:
        _WIKI_POSTGRES_PARITY_WARNING_TOTAL[(_label_value(market), _label_value(diff_code))] += increment


def record_wiki_postgres_parity_summary(summary: Mapping[str, Any] | None) -> None:
    if not isinstance(summary, Mapping):
        return
    results: list[Any] = []
    for key in ("wiki_postgres_parity_results", "production_sample_wiki_postgres_parity_results"):
        items = summary.get(key)
        if isinstance(items, list):
            results.extend(items)
    with _METRICS_LOCK:
        for result in results:
            if not isinstance(result, Mapping) or result.get("skipped"):
                continue
            market = _label_value(result.get("market"))
            code_counts = result.get("warning_diff_code_counts")
            if isinstance(code_counts, Mapping):
                for code, count in code_counts.items():
                    try:
                        increment = int(count or 0)
                    except (TypeError, ValueError):
                        increment = 0
                    if increment > 0:
                        _WIKI_POSTGRES_PARITY_WARNING_TOTAL[(market, _label_value(code))] += increment
                continue
            warnings = result.get("warnings")
            if isinstance(warnings, list):
                for _warning in warnings:
                    _WIKI_POSTGRES_PARITY_WARNING_TOTAL[(market, "uncategorized")] += 1


def record_frontend_pipeline_job_failure(
    *,
    market: str | None,
    action: str,
    reason: str | None = None,
) -> None:
    with _METRICS_LOCK:
        _FRONTEND_PIPELINE_JOB_FAILURE_TOTAL[
            (_label_value(market), _label_value(action), _label_value(reason or "unknown"))
        ] += 1


def record_background_job_final_state(
    *,
    kind: str | None,
    status: str | None,
    duration_seconds: int | float,
) -> None:
    key = (_label_value(kind), _label_value(status))
    duration = max(0.0, float(duration_seconds or 0))
    with _METRICS_LOCK:
        _BACKGROUND_JOB_FINAL_STATE_TOTAL[key] += 1
        bucket = _BACKGROUND_JOB_DURATION_SECONDS[key]
        bucket["count"] += 1
        bucket["sum"] += duration
        bucket["max"] = max(bucket["max"], duration)


def record_background_job_persistence_failure(*, operation: str | None) -> None:
    with _METRICS_LOCK:
        _BACKGROUND_JOB_PERSISTENCE_FAILURE_TOTAL[_label_value(operation)] += 1


def metrics_snapshot() -> dict[str, Any]:
    with _METRICS_LOCK:
        request_count = sum(_HTTP_REQUEST_TOTAL.values())
        error_count = sum(count for (_method, _path, status), count in _HTTP_REQUEST_TOTAL.items() if status.startswith("5"))
        answer_trace_count = sum(_ANSWER_GUARDRAIL_BLOCK_TOTAL.values())
        return {
            "uptime_seconds": max(0, int(time.time() - _START_TIME)),
            "request_count": int(request_count),
            "request_error_count": int(error_count),
            "answer_trace_count": int(answer_trace_count),
            "agent_fact_source_counts": dict(_AGENT_FACT_SOURCE_TOTAL),
            "postgres_fallback_reason_counts": dict(_POSTGRES_FALLBACK_REASON_TOTAL),
            "answer_guardrail_block_counts": dict(_ANSWER_GUARDRAIL_BLOCK_TOTAL),
            "answer_calculator_run_count": int(_ANSWER_CALCULATOR_RUN_TOTAL),
            "answer_citation_count": int(_ANSWER_CITATION_TOTAL),
            "ingestion_duration_seconds": {
                "|".join(key): dict(value) for key, value in _INGESTION_DURATION_SECONDS.items()
            },
            "ingestion_fact_counts": {"|".join(key): value for key, value in _INGESTION_FACT_COUNT.items()},
            "wiki_postgres_parity_warning_counts": {
                "|".join(key): value for key, value in _WIKI_POSTGRES_PARITY_WARNING_TOTAL.items()
            },
            "frontend_pipeline_job_failure_counts": {
                "|".join(key): value for key, value in _FRONTEND_PIPELINE_JOB_FAILURE_TOTAL.items()
            },
            "background_job_final_state_counts": {
                "|".join(key): value for key, value in _BACKGROUND_JOB_FINAL_STATE_TOTAL.items()
            },
            "background_job_duration_seconds": {
                "|".join(key): dict(value) for key, value in _BACKGROUND_JOB_DURATION_SECONDS.items()
            },
            "background_job_persistence_failure_counts": dict(_BACKGROUND_JOB_PERSISTENCE_FAILURE_TOTAL),
        }


def render_prometheus_metrics() -> str:
    lines = [
        "# HELP siq_api_uptime_seconds API process uptime in seconds.",
        "# TYPE siq_api_uptime_seconds gauge",
        f"siq_api_uptime_seconds {max(0, int(time.time() - _START_TIME))}",
        "# HELP siq_api_request_total HTTP requests by method, path, and status code.",
        "# TYPE siq_api_request_total counter",
    ]
    with _METRICS_LOCK:
        for (method, path, status), count in sorted(_HTTP_REQUEST_TOTAL.items()):
            lines.append(
                f'siq_api_request_total{{method="{_prom_label(method)}",path="{_prom_label(path)}",status_code="{_prom_label(status)}"}} {count}'
            )
        lines.extend(
            [
                "# HELP siq_api_request_duration_ms_sum Total HTTP request duration in milliseconds.",
                "# TYPE siq_api_request_duration_ms_sum counter",
            ]
        )
        for (method, path), payload in sorted(_HTTP_REQUEST_DURATION_MS.items()):
            labels = f'method="{_prom_label(method)}",path="{_prom_label(path)}"'
            lines.append(f"siq_api_request_duration_ms_sum{{{labels}}} {payload['sum']:.3f}")
            lines.append(f"siq_api_request_duration_ms_count{{{labels}}} {int(payload['count'])}")
            lines.append(f"siq_api_request_duration_ms_max{{{labels}}} {payload['max']:.3f}")
        lines.extend(
            [
                "# HELP siq_agent_fact_source_total Answer audit facts/citations by source_type.",
                "# TYPE siq_agent_fact_source_total counter",
            ]
        )
        for source_type, count in sorted(_AGENT_FACT_SOURCE_TOTAL.items()):
            lines.append(f'siq_agent_fact_source_total{{source_type="{_prom_label(source_type)}"}} {count}')
        lines.extend(
            [
                "# HELP siq_postgres_fallback_reason_total PostgreSQL fallback reasons observed in answer audit traces.",
                "# TYPE siq_postgres_fallback_reason_total counter",
            ]
        )
        for reason, count in sorted(_POSTGRES_FALLBACK_REASON_TOTAL.items()):
            lines.append(f'siq_postgres_fallback_reason_total{{reason="{_prom_label(reason)}"}} {count}')
        lines.extend(
            [
                "# HELP siq_answer_guardrail_block_total Answer audit traces grouped by guardrail blocked status.",
                "# TYPE siq_answer_guardrail_block_total counter",
            ]
        )
        for blocked, count in sorted(_ANSWER_GUARDRAIL_BLOCK_TOTAL.items()):
            lines.append(f'siq_answer_guardrail_block_total{{blocked="{blocked}"}} {count}')
        lines.extend(
            [
                "# HELP siq_answer_calculator_run_total Calculator runs observed in answer audit traces.",
                "# TYPE siq_answer_calculator_run_total counter",
                f"siq_answer_calculator_run_total {_ANSWER_CALCULATOR_RUN_TOTAL}",
                "# HELP siq_answer_citation_total Citations observed in answer audit traces.",
                "# TYPE siq_answer_citation_total counter",
                f"siq_answer_citation_total {_ANSWER_CITATION_TOTAL}",
            ]
        )
        lines.extend(
            [
                "# HELP siq_ingestion_duration_seconds_sum Total ingestion duration in seconds by market, stage, and status.",
                "# TYPE siq_ingestion_duration_seconds_sum counter",
            ]
        )
        for (market, stage, status), payload in sorted(_INGESTION_DURATION_SECONDS.items()):
            labels = (
                f'market="{_prom_label(market)}",stage="{_prom_label(stage)}",status="{_prom_label(status)}"'
            )
            lines.append(f"siq_ingestion_duration_seconds_sum{{{labels}}} {payload['sum']:.6f}")
            lines.append(f"siq_ingestion_duration_seconds_count{{{labels}}} {int(payload['count'])}")
            lines.append(f"siq_ingestion_duration_seconds_max{{{labels}}} {payload['max']:.6f}")
        lines.extend(
            [
                "# HELP siq_ingestion_fact_count Latest observed ingestion row counts by market and kind.",
                "# TYPE siq_ingestion_fact_count gauge",
            ]
        )
        for (market, kind), value in sorted(_INGESTION_FACT_COUNT.items()):
            lines.append(f'siq_ingestion_fact_count{{market="{_prom_label(market)}",kind="{_prom_label(kind)}"}} {value:.0f}')
        lines.extend(
            [
                "# HELP siq_wiki_postgres_parity_warning_total Offline Wiki/PostgreSQL parity warnings by market and diff code.",
                "# TYPE siq_wiki_postgres_parity_warning_total counter",
            ]
        )
        for (market, diff_code), count in sorted(_WIKI_POSTGRES_PARITY_WARNING_TOTAL.items()):
            lines.append(
                f'siq_wiki_postgres_parity_warning_total{{market="{_prom_label(market)}",diff_code="{_prom_label(diff_code)}"}} {count}'
            )
        lines.extend(
            [
                "# HELP siq_frontend_pipeline_job_failure_total Frontend-triggered ingestion job failures by market, action, and reason.",
                "# TYPE siq_frontend_pipeline_job_failure_total counter",
            ]
        )
        for (market, action, reason), count in sorted(_FRONTEND_PIPELINE_JOB_FAILURE_TOTAL.items()):
            lines.append(
                f'siq_frontend_pipeline_job_failure_total{{market="{_prom_label(market)}",action="{_prom_label(action)}",reason="{_prom_label(reason)}"}} {count}'
            )
        lines.extend(
            [
                "# HELP siq_background_job_final_state_total Background jobs by kind and terminal status.",
                "# TYPE siq_background_job_final_state_total counter",
            ]
        )
        for (kind, status), count in sorted(_BACKGROUND_JOB_FINAL_STATE_TOTAL.items()):
            lines.append(
                f'siq_background_job_final_state_total{{kind="{_prom_label(kind)}",status="{_prom_label(status)}"}} {count}'
            )
        lines.extend(
            [
                "# HELP siq_background_job_duration_seconds_sum Total background job duration in seconds by kind and terminal status.",
                "# TYPE siq_background_job_duration_seconds_sum counter",
            ]
        )
        for (kind, status), payload in sorted(_BACKGROUND_JOB_DURATION_SECONDS.items()):
            labels = f'kind="{_prom_label(kind)}",status="{_prom_label(status)}"'
            lines.append(f"siq_background_job_duration_seconds_sum{{{labels}}} {payload['sum']:.6f}")
            lines.append(f"siq_background_job_duration_seconds_count{{{labels}}} {int(payload['count'])}")
            lines.append(f"siq_background_job_duration_seconds_max{{{labels}}} {payload['max']:.6f}")
        lines.extend(
            [
                "# HELP siq_background_job_persistence_failure_total Background job store persistence failures.",
                "# TYPE siq_background_job_persistence_failure_total counter",
            ]
        )
        for operation, count in sorted(_BACKGROUND_JOB_PERSISTENCE_FAILURE_TOTAL.items()):
            lines.append(
                "siq_background_job_persistence_failure_total"
                f'{{operation="{_prom_label(operation)}"}} {count}'
            )
    return "\n".join(lines) + "\n"


def reset_observability_metrics_for_tests() -> None:
    with _METRICS_LOCK:
        _HTTP_REQUEST_TOTAL.clear()
        _HTTP_REQUEST_DURATION_MS.clear()
        _AGENT_FACT_SOURCE_TOTAL.clear()
        _POSTGRES_FALLBACK_REASON_TOTAL.clear()
        _ANSWER_GUARDRAIL_BLOCK_TOTAL.clear()
        _INGESTION_DURATION_SECONDS.clear()
        _INGESTION_FACT_COUNT.clear()
        _WIKI_POSTGRES_PARITY_WARNING_TOTAL.clear()
        _FRONTEND_PIPELINE_JOB_FAILURE_TOTAL.clear()
        _BACKGROUND_JOB_FINAL_STATE_TOTAL.clear()
        _BACKGROUND_JOB_PERSISTENCE_FAILURE_TOTAL.clear()
        _BACKGROUND_JOB_DURATION_SECONDS.clear()
        global _ANSWER_CALCULATOR_RUN_TOTAL, _ANSWER_CITATION_TOTAL
        _ANSWER_CALCULATOR_RUN_TOTAL = 0
        _ANSWER_CITATION_TOTAL = 0
