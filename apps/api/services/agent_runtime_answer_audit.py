"""Answer audit trace helpers for chat runtime final replies."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services import agent_runtime_context, observability
from services.agent_runtime_financial_claim_verifier import (
    claim_verification_payload,
    extract_structured_calculation_runs,
    materialize_evidence_bound_calculation_runs,
    materialize_runtime_calculation_runs,
    validate_calculation_traces,
    verify_financial_claims,
)
from services.path_config import BACKEND_DATA_ROOT

ANSWER_AUDIT_TRACE_SCHEMA = "siq_answer_audit_trace_v1"
ANSWER_AUDIT_TRACE_ID_PREFIX = "aat_"
RECENT_ANSWER_AUDIT_TRACE_LIMIT = 200
RECENT_ANSWER_AUDIT_TRACES: list[dict[str, Any]] = []

REDACTED = "[REDACTED]"
REDACTED_DATABASE_URL = "[REDACTED_DATABASE_URL]"

_SENSITIVE_KEY_TERMS = (
    "access_token",
    "api_key",
    "authorization",
    "connection_string",
    "cookie",
    "credential",
    "database_url",
    "db_url",
    "dsn",
    "password",
    "passwd",
    "pg_dsn",
    "pg_url",
    "postgres_url",
    "postgresql_url",
    "pwd",
    "secret",
    "source_token",
    "token",
)
_DB_URL_RE = re.compile(
    r"\b(?:postgres(?:ql)?|mysql|mariadb|mssql|oracle|redshift|snowflake)://[^\s\"'<>),]+",
    re.IGNORECASE,
)
_URL_CREDENTIAL_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9+.-]*://)([^/\s:@]+):([^@\s/]+)@")
_QUERY_SECRET_RE = re.compile(
    r"(?i)([?&](?:access_token|api_key|password|secret|source_token|token)=)[^&\s#]+"
)
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:access_token|api_key|database_url|db_url|dsn|password|passwd|"
    r"pg_dsn|postgres_url|postgresql_url|pwd|secret|source_token|token)[A-Za-z0-9_.-]*)"
    r"\s*([:=])\s*['\"]?(?!\[REDACTED(?:_DATABASE_URL)?\])[^'\"\s,;)}\]]+"
)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/\-]+=*")
_SOURCE_FIELD_START_RE = re.compile(r"(?:(?<=^)|(?<=[\s,，;；|]))([A-Za-z_][A-Za-z0-9_]*)=")
_SOURCE_FIELD_NAMES = frozenset(
    {
        "bbox",
        "canonical_name",
        "chunk_index",
        "company_id",
        "concept",
        "currency",
        "evidence_count",
        "evidence_id",
        "effective_date",
        "fact_currency",
        "file",
        "filing_id",
        "html_anchor",
        "jurisdiction",
        "label",
        "law_article",
        "market",
        "md_line",
        "metric",
        "metric_name",
        "name",
        "page",
        "pdf_page",
        "pdf_page_number",
        "period",
        "period_key",
        "presentation_currency",
        "parse_run_id",
        "quote",
        "quote_text",
        "raw_value",
        "report_id",
        "reporting_currency",
        "scale",
        "schema",
        "source",
        "source_page",
        "source_path",
        "source_type",
        "source_url",
        "statement_id",
        "statement_type",
        "table",
        "table_index",
        "task_id",
        "unit",
        "value",
        "wiki_report_path",
        "relevance",
    }
)
_QUESTION_ID_RE = re.compile(r"(?i)\b(?:question_id|questionId|qid)\s*[:=]\s*([A-Za-z0-9_.:/#-]+)")
_OPERATION_RE = re.compile(r"(?i)\boperation\s*[:=]\s*([A-Za-z0-9_.:/#-]+)")
_GUARDRAIL_STATUS_RE = re.compile(r"(?i)\bguardrail_status\s*[:=]\s*([A-Za-z0-9_.:/#-]+)")
_GUARDRAIL_REASON_RE = re.compile(r"(?i)\bguardrail_reason\s*[:=]\s*([A-Za-z0-9_.:/#-]+)")
_AUDIT_SUMMARY_HEADING_RE = re.compile(r"(?m)^(?:#{1,4}\s+)?审计详情[:：]?\s*$")
_TRACE_ID_RE = re.compile(r"^aat_[a-f0-9]{32}$")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _default_log_path() -> Path:
    raw = os.getenv("SIQ_ANSWER_AUDIT_TRACE_LOG_PATH")
    if raw and raw.strip():
        return Path(raw).expanduser()
    return BACKEND_DATA_ROOT / "audit" / "answer_audit_trace.jsonl"


def _stable_value(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _stable_value(value.model_dump(exclude_none=True))
    if isinstance(value, Mapping):
        return {str(key): _stable_value(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    if isinstance(value, set):
        return [_stable_value(item) for item in sorted(value, key=lambda item: repr(item))]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        _stable_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def is_answer_audit_trace_id(value: Any) -> bool:
    return bool(_TRACE_ID_RE.fullmatch(str(value or "").strip()))


def answer_audit_trace_id(record: Mapping[str, Any]) -> str:
    existing = str(record.get("trace_id") or "").strip()
    if is_answer_audit_trace_id(existing):
        return existing
    basis = {
        "schema_version": record.get("schema_version") or ANSWER_AUDIT_TRACE_SCHEMA,
        "created_at": record.get("created_at"),
        "profile": record.get("profile"),
        "session_id": record.get("session_id"),
        "question_id": record.get("question_id"),
        "message_hash": record.get("message_hash"),
        "answer_hash": record.get("answer_hash"),
    }
    return f"{ANSWER_AUDIT_TRACE_ID_PREFIX}{stable_hash(basis)[:32]}"


def _with_trace_id(record: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(redact_audit_value(record, max_string_length=1200))
    payload["trace_id"] = answer_audit_trace_id(payload)
    return payload


def _truncate_text(value: str, limit: int = 1200) -> str:
    if len(value) <= limit:
        return value
    return value[:limit].rstrip() + "...[truncated]"


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9_]+", "_", str(key).lower())
    return any(term in normalized for term in _SENSITIVE_KEY_TERMS)


def _redact_sensitive_text(value: str) -> str:
    redacted = _DB_URL_RE.sub(REDACTED_DATABASE_URL, value)
    redacted = _URL_CREDENTIAL_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}:{REDACTED}@", redacted)
    redacted = _QUERY_SECRET_RE.sub(lambda match: f"{match.group(1)}{REDACTED}", redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", redacted)
    redacted = _BEARER_RE.sub(f"Bearer {REDACTED}", redacted)
    return redacted


def redact_audit_value(value: Any, *, max_string_length: int = 1200) -> Any:
    if hasattr(value, "model_dump"):
        return redact_audit_value(value.model_dump(exclude_none=True), max_string_length=max_string_length)
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _is_sensitive_key(str(key)) else redact_audit_value(item, max_string_length=max_string_length)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_audit_value(item, max_string_length=max_string_length) for item in value]
    if isinstance(value, tuple):
        return [redact_audit_value(item, max_string_length=max_string_length) for item in value]
    if isinstance(value, set):
        return [redact_audit_value(item, max_string_length=max_string_length) for item in sorted(value, key=lambda item: repr(item))]
    if isinstance(value, Path):
        return _truncate_text(_redact_sensitive_text(str(value)), max_string_length)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return _truncate_text(_redact_sensitive_text(value), max_string_length)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return _truncate_text(_redact_sensitive_text(str(value)), max_string_length)


def _normalized_lookup_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _find_context_value(value: Any, keys: set[str], *, depth: int = 0) -> Any | None:
    if depth > 8:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _normalized_lookup_key(key) in keys and item not in (None, "", [], {}):
                return item
        for item in value.values():
            found = _find_context_value(item, keys, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in value:
            found = _find_context_value(item, keys, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    return None


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_question_id(message: str, context: Any | None) -> str | None:
    value = _find_context_value(context, {"questionid", "qid"})
    if value not in (None, "", [], {}):
        return str(redact_audit_value(value, max_string_length=256))
    match = _QUESTION_ID_RE.search(message or "")
    if match:
        return str(redact_audit_value(match.group(1), max_string_length=256))
    return None


def _extract_source_fields(raw_line: str) -> dict[str, str]:
    matches = [
        match
        for match in _SOURCE_FIELD_START_RE.finditer(raw_line or "")
        if match.group(1) in _SOURCE_FIELD_NAMES
    ]
    fields: dict[str, str] = {}
    for index, match in enumerate(matches):
        key = match.group(1).strip()
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_line)
        value = raw_line[start:end].strip().strip(" \t,，;；|。")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1].strip()
        if value:
            fields[key] = value
    return fields


def _extract_source_references(reply: str) -> list[dict[str, Any]]:
    references: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate((reply or "").splitlines(), start=1):
        has_runtime_source = "source_type=" in raw_line
        has_legal_source = all(marker in raw_line for marker in ("source=", "source_path=", "chunk_index="))
        if not has_runtime_source and not has_legal_source:
            continue
        fields = _extract_source_fields(raw_line)
        source_type = str(fields.get("source_type") or "").strip()
        if not source_type and all(fields.get(key) for key in ("source", "source_path", "chunk_index")):
            source_type = "legal_corpus"
            fields["source_type"] = source_type
        if not source_type:
            continue
        label_match = re.match(r"\s*\|?\s*(\[[^\]]+\])", raw_line)
        reference: dict[str, Any] = {
            "line_number": line_number,
            "source_type": source_type,
            **fields,
        }
        if label_match:
            reference["label"] = label_match.group(1)
        reference["raw"] = raw_line.strip()
        identity = json.dumps(
            {
                key: reference.get(key)
                for key in (
                    "label",
                    "source_type",
                    "source",
                    "source_path",
                    "chunk_index",
                    "law_article",
                    "jurisdiction",
                    "effective_date",
                    "file",
                    "market",
                    "schema",
                    "table",
                    "statement_id",
                    "statement_type",
                    "company_id",
                    "filing_id",
                    "parse_run_id",
                    "report_id",
                    "metric",
                    "metric_name",
                    "canonical_name",
                    "name",
                    "concept",
                    "period",
                    "period_key",
                    "value",
                    "raw_value",
                    "unit",
                    "currency",
                    "scale",
                    "task_id",
                    "pdf_page",
                    "pdf_page_number",
                    "source_page",
                    "table_index",
                    "html_anchor",
                    "evidence_id",
                    "md_line",
                    "relevance",
                )
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        if identity in seen:
            continue
        seen.add(identity)
        references.append(redact_audit_value(reference, max_string_length=1200))
        if len(references) >= 100:
            break
    return references


def _fact_from_reference(reference: Mapping[str, Any]) -> dict[str, Any]:
    preferred_keys = (
        "label",
        "source_type",
        "source",
        "source_path",
        "chunk_index",
        "law_article",
        "jurisdiction",
        "effective_date",
        "market",
        "schema",
        "file",
        "table",
        "statement_id",
        "statement_type",
        "company_id",
        "filing_id",
        "parse_run_id",
        "report_id",
        "metric",
        "metric_name",
        "canonical_name",
        "name",
        "concept",
        "period",
        "period_key",
        "value",
        "raw_value",
        "unit",
        "currency",
        "scale",
        "fact_currency",
        "reporting_currency",
        "presentation_currency",
        "task_id",
        "pdf_page",
        "pdf_page_number",
        "source_page",
        "table_index",
        "html_anchor",
        "evidence_id",
        "md_line",
        "bbox",
        "quote",
        "quote_text",
        "source_url",
        "wiki_report_path",
        "relevance",
        "line_number",
    )
    fact = {key: reference[key] for key in preferred_keys if key in reference and reference[key] not in (None, "")}
    if "metric_name" not in fact:
        metric_name = _first_text(fact.get("metric"), fact.get("name"), fact.get("concept"))
        if metric_name:
            fact["metric_name"] = metric_name
    if "source_page" not in fact:
        source_page = _first_text(fact.get("pdf_page"), fact.get("pdf_page_number"))
        if source_page:
            fact["source_page"] = source_page
    if "quote" not in fact and fact.get("quote_text"):
        fact["quote"] = fact["quote_text"]
    return fact


def _source_types(references: Sequence[Mapping[str, Any]]) -> list[str]:
    values = sorted({str(item.get("source_type") or "") for item in references if item.get("source_type")})
    return values


def _is_legal_reference(reference: Mapping[str, Any]) -> bool:
    source_type = str(reference.get("source_type") or "")
    if source_type.startswith("legal"):
        return True
    return bool(reference.get("source_path") and reference.get("chunk_index") and reference.get("source"))


def _extract_resolved_company(context: Any | None, references: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    company = _find_context_value(context, {"resolvedcompany", "company"})
    output: dict[str, Any] = {}
    if isinstance(company, Mapping):
        output = {
            "id": _first_text(company.get("id"), company.get("company_id"), company.get("resolved_company_id")),
            "name": _first_text(
                company.get("name"),
                company.get("company_name"),
                company.get("company_short_name"),
                company.get("company_full_name"),
                company.get("resolved_stock_name"),
            ),
            "code": _first_text(company.get("code"), company.get("stock_code"), company.get("resolved_stock_code")),
            "market": _first_text(company.get("market"), company.get("exchange")),
            "dir": _first_text(company.get("dir"), company.get("path"), company.get("company_path")),
        }
    elif company not in (None, "", [], {}):
        output = {"name": str(company)}

    fallback_keys = {
        "id": {"resolvedcompanyid", "companyid"},
        "name": {"resolvedcompanyname", "companyname", "resolvedstockname"},
        "code": {"stockcode", "resolvedstockcode", "code"},
        "market": {"market", "exchange"},
    }
    for target_key, lookup_keys in fallback_keys.items():
        if output.get(target_key):
            continue
        value = _find_context_value(context, lookup_keys)
        if value not in (None, "", [], {}):
            output[target_key] = str(value)

    for reference in references:
        if output.get("id"):
            break
        company_id = reference.get("company_id") or reference.get("resolved_company_id")
        if company_id:
            output["id"] = str(company_id)

    output = {key: value for key, value in output.items() if value not in (None, "")}
    return redact_audit_value(output, max_string_length=500) if output else None


def _extract_resolved_period(context: Any | None, references: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    period = _find_context_value(context, {"resolvedperiod", "period", "reportperiod", "filingperiod"})
    output: dict[str, Any] = {}
    if isinstance(period, Mapping):
        output = {
            "fiscal_year": _first_text(period.get("fiscal_year"), period.get("report_year"), period.get("year")),
            "period_end": _first_text(period.get("period_end"), period.get("filing_period_end"), period.get("date")),
            "report_id": _first_text(period.get("report_id"), period.get("reportId")),
            "filing_id": _first_text(period.get("filing_id"), period.get("filingId")),
            "parse_run_id": _first_text(period.get("parse_run_id"), period.get("parseRunId")),
        }
    elif period not in (None, "", [], {}):
        output["period"] = str(period)

    context_fallbacks = {
        "fiscal_year": {"fiscalyear", "reportyear", "year"},
        "period_end": {"periodend", "filingperiodend"},
        "report_id": {"reportid"},
        "filing_id": {"filingid"},
        "parse_run_id": {"parserunid", "parse_run_id"},
    }
    for target_key, lookup_keys in context_fallbacks.items():
        if output.get(target_key):
            continue
        value = _find_context_value(context, lookup_keys)
        if value not in (None, "", [], {}):
            output[target_key] = str(value)

    for reference in references:
        if not output.get("period"):
            output["period"] = _first_text(reference.get("period"), reference.get("period_key"))
        if not output.get("period_key") and reference.get("period_key"):
            output["period_key"] = str(reference.get("period_key"))
        if not output.get("report_id") and reference.get("report_id"):
            output["report_id"] = str(reference.get("report_id"))
        if not output.get("filing_id") and reference.get("filing_id"):
            output["filing_id"] = str(reference.get("filing_id"))
        if not output.get("parse_run_id") and reference.get("parse_run_id"):
            output["parse_run_id"] = str(reference.get("parse_run_id"))

    output = {key: value for key, value in output.items() if value not in (None, "")}
    return redact_audit_value(output, max_string_length=500) if output else None


def _extract_query_plan(context: Any | None, references: Sequence[Mapping[str, Any]]) -> Any | None:
    value = _find_context_value(context, {"queryplan", "queryplanning", "retrievalplan"})
    observed = {"observed_source_types": _source_types(references)}
    if value not in (None, "", [], {}):
        if isinstance(value, Mapping):
            return redact_audit_value({**dict(value), **observed}, max_string_length=1200)
        return redact_audit_value({"plan": value, **observed}, max_string_length=1200)
    if observed["observed_source_types"]:
        return observed
    return None


def _extract_fallback_reason(context: Any | None, references: Sequence[Mapping[str, Any]], reply: str) -> str | None:
    events = _find_context_value(context, {"auditfallbackevents", "fallbackevents", "postgresfallbackevents"})
    if isinstance(events, Sequence) and not isinstance(events, (str, bytes, bytearray)):
        reasons = [
            str(item.get("reason") or item.get("stage") or "").strip()
            for item in events
            if isinstance(item, Mapping) and (item.get("reason") or item.get("stage"))
        ]
        for preferred in (
            "research_identity_incomplete",
            "market_view_hit",
            "postgres_hit",
            "postgres_unavailable",
            "research_identity_report_mismatch",
            "wiki_fulltext_miss",
            "wiki_structured_miss",
        ):
            if preferred in reasons:
                return preferred
        if reasons:
            return reasons[-1]
    elif isinstance(events, Mapping):
        reason = str(events.get("reason") or events.get("stage") or "").strip()
        if reason:
            return reason
    value = _find_context_value(context, {"fallbackreason", "fallbackcause"})
    if value not in (None, "", [], {}):
        return str(redact_audit_value(value, max_string_length=500))
    source_types = _source_types(references)
    if any(source_type.startswith("postgres") or source_type == "postgresql" for source_type in source_types):
        return "postgresql_fallback_used"
    if any("fallback" in source_type or "fulltext" in source_type for source_type in source_types):
        return "wiki_fulltext_fallback_used"
    text = reply or ""
    if "数据库 fallback" in text or "PostgreSQL fallback" in text:
        return "postgresql_fallback_used"
    return None


def _extract_calculator_runs(
    context: Any | None,
    reply: str,
    *,
    trusted_calculation_runs: Sequence[Mapping[str, Any]] = (),
    trusted_calculation_evidence: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    context_runs = _find_context_value(context, {"calculatorruns", "calculationruns", "calculatortrace"})
    if isinstance(context_runs, Sequence) and not isinstance(context_runs, (str, bytes, bytearray)):
        for item in context_runs:
            runs.append(
                {
                    "source": "context_unvalidated",
                    "validated": False,
                    "payload": redact_audit_value(item, max_string_length=1200),
                }
            )
    elif context_runs not in (None, "", [], {}):
        runs.append(
            {
                "source": "context_unvalidated",
                "validated": False,
                "payload": redact_audit_value(context_runs, max_string_length=1200),
            }
        )

    for payload in materialize_runtime_calculation_runs(
        trusted_calculation_runs,
        reply,
        expected_identity=agent_runtime_context.research_identity(context),
    ):
        runs.append(
            {
                "source": "runtime_tool_receipt",
                "schema_version": str(payload.get("schema_version") or ""),
                "tool": str(payload.get("tool") or ""),
                "operation": str(payload.get("operation") or ""),
                "metric": str(payload.get("metric") or ""),
                "period": str(payload.get("period") or ""),
                "validated": True,
                "payload": redact_audit_value(payload, max_string_length=1200),
            }
        )

    for payload in materialize_evidence_bound_calculation_runs(
        reply,
        trusted_calculation_evidence,
        expected_identity=agent_runtime_context.research_identity(context),
        require_reconciliation="勾稽校验" in reply or "financial_reconciliation_validator.py" in reply,
    ):
        runs.append(
            {
                "source": "backend_evidence_recompute",
                "schema_version": str(payload.get("schema_version") or ""),
                "tool": str(payload.get("tool") or ""),
                "operation": str(payload.get("operation") or ""),
                "metric": str(payload.get("metric") or ""),
                "period": str(payload.get("period") or ""),
                "validated": True,
                "payload": redact_audit_value(payload, max_string_length=1200),
            }
        )

    for payload in extract_structured_calculation_runs(reply):
        runs.append(
            {
                "source": "reply_structured",
                "schema_version": str(payload.get("schema_version") or ""),
                "tool": str(payload.get("tool") or ""),
                "operation": str(payload.get("operation") or ""),
                "metric": str(payload.get("metric") or ""),
                "period": str(payload.get("period") or ""),
                "validated": True,
                "payload": redact_audit_value(payload, max_string_length=1200),
            }
        )

    active_section = ""
    for line_number, raw_line in enumerate((reply or "").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if "计算器校验" in stripped or "勾稽校验" in stripped:
                active_section = stripped.lstrip("#").strip()
                runs.append(
                    {
                        "source": "reply_marker",
                        "validated": False,
                        "line_number": line_number,
                        "section": active_section,
                    }
                )
            else:
                active_section = ""
            continue
        has_tool = "financial_calculator.py" in stripped or "financial_reconciliation_validator.py" in stripped
        operation_match = _OPERATION_RE.search(stripped)
        if not (active_section or has_tool or operation_match):
            continue
        item: dict[str, Any] = {
            "source": "reply_marker",
            "validated": False,
            "line_number": line_number,
            "line": stripped,
        }
        if active_section:
            item["section"] = active_section
        if "financial_reconciliation_validator.py" in stripped or "勾稽" in stripped:
            item["tool"] = "financial_reconciliation_validator.py"
        elif "financial_calculator.py" in stripped or operation_match:
            item["tool"] = "financial_calculator.py"
        if operation_match:
            item["operation"] = operation_match.group(1)
        runs.append(redact_audit_value(item, max_string_length=1200))
        if len(runs) >= 50:
            break
    return runs


def _extract_guardrail_marker_result(reply: str) -> dict[str, Any]:
    text = reply or ""
    status_match = _GUARDRAIL_STATUS_RE.search(text)
    reason_match = _GUARDRAIL_REASON_RE.search(text)
    status = str(status_match.group(1) if status_match else "").strip().lower()
    reason = str(reason_match.group(1) if reason_match else "").strip()
    if status not in {"blocked", "denied", "rejected", "warning"} and not reason:
        return {}
    payload: dict[str, Any] = {}
    if status:
        payload["status"] = status
    if status == "warning":
        payload["blocked"] = False
        payload["allowed"] = True
    elif status in {"blocked", "denied", "rejected"} or reason:
        payload["blocked"] = True
        payload["allowed"] = False
    if reason:
        payload["reason"] = reason
    return payload


def _build_guardrail_result(
    *,
    raw_reply: str | None,
    final_reply: str,
    references: Sequence[Mapping[str, Any]],
    calculator_runs: Sequence[Mapping[str, Any]],
    enforce_evidence_contract: bool,
    guardrail_result: Mapping[str, Any] | None,
) -> dict[str, Any]:
    computed: dict[str, Any] = {
        "blocked": False,
        "allowed": True,
        "evidence_contract_enforced": enforce_evidence_contract,
        "output_was_guarded": raw_reply is not None and raw_reply != final_reply,
        "final_reply_hash": stable_hash(redact_audit_value(final_reply, max_string_length=20000)),
        "citation_count": len(references),
        "source_types": _source_types(references),
        "has_wiki_facts": any(str(item.get("source_type") or "").startswith("wiki") for item in references),
        "has_postgres_facts": any(
            str(item.get("source_type") or "").startswith("postgres") or item.get("source_type") == "postgresql"
            for item in references
        ),
        "has_legal_facts": any(_is_legal_reference(item) for item in references),
        "has_calculator_runs": any(item.get("validated") is True for item in calculator_runs),
        "calculation_warning_appended": any(
            heading in (final_reply or "")
            for heading in ("## 计算校验提示", "## 计算校验缺失", "## 计算校验无效")
        ),
        "tool_availability_correction_appended": "## 工具状态纠正" in (final_reply or ""),
    }
    marker_result = _extract_guardrail_marker_result(final_reply or "")
    if marker_result:
        computed.update(marker_result)
    if raw_reply is not None:
        computed["raw_reply_hash"] = stable_hash(redact_audit_value(raw_reply, max_string_length=20000))
    if guardrail_result:
        computed.update(dict(redact_audit_value(guardrail_result, max_string_length=1200)))
    return computed


def _has_actionable_legacy_marker(
    text: str,
    *,
    section_title: str,
    tool_name: str,
) -> bool:
    """Ignore headings that explicitly say no calculation was performed."""
    lines = (text or "").splitlines()
    in_calculation_section = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            in_calculation_section = stripped == section_title
            continue
        lowered = stripped.lower()
        negated = any(marker in stripped for marker in ("未计算", "不计算", "不要计算", "无需计算", "未使用", "不涉及"))
        if negated:
            continue
        if tool_name in lowered:
            return True
        if in_calculation_section and ("operation=" in lowered or '"operation"' in lowered):
            return True
    return False


def build_answer_audit_trace(
    *,
    message: str,
    final_reply: str,
    context: Any | None = None,
    profile: Any | None = None,
    session_id: str | None = None,
    raw_reply: str | None = None,
    enforce_evidence_contract: bool = True,
    guardrail_result: Mapping[str, Any] | None = None,
    trusted_calculation_runs: Sequence[Mapping[str, Any]] = (),
    trusted_calculation_evidence: Sequence[Mapping[str, Any]] = (),
    created_at: datetime | str | None = None,
) -> dict[str, Any]:
    sanitized_message = str(redact_audit_value(message or "", max_string_length=2000))
    sanitized_reply = str(redact_audit_value(final_reply or "", max_string_length=2000))
    references = _extract_source_references(final_reply or "")
    wiki_facts = [
        _fact_from_reference(reference)
        for reference in references
        if str(reference.get("source_type") or "").startswith("wiki")
    ]
    postgres_facts = [
        _fact_from_reference(reference)
        for reference in references
        if str(reference.get("source_type") or "").startswith("postgres") or reference.get("source_type") == "postgresql"
    ]
    legal_facts = [
        _fact_from_reference(reference)
        for reference in references
        if _is_legal_reference(reference)
    ]
    calculator_runs = _extract_calculator_runs(
        context,
        final_reply or "",
        trusted_calculation_runs=trusted_calculation_runs,
        trusted_calculation_evidence=trusted_calculation_evidence,
    )
    calculation_trace_reply = raw_reply if raw_reply is not None else final_reply or ""
    if (trusted_calculation_runs or trusted_calculation_evidence) and final_reply and final_reply not in calculation_trace_reply:
        calculation_trace_reply = f"{calculation_trace_reply}\n{final_reply}"
    structured_calculation_runs = extract_structured_calculation_runs(calculation_trace_reply)
    has_legacy_calculation_marker = _has_actionable_legacy_marker(
        calculation_trace_reply,
        section_title="## 计算器校验",
        tool_name="financial_calculator.py",
    )
    has_legacy_reconciliation_marker = _has_actionable_legacy_marker(
        calculation_trace_reply,
        section_title="## 勾稽校验",
        tool_name="financial_reconciliation_validator.py",
    )
    calculation_trace_validation = validate_calculation_traces(
        calculation_trace_reply,
        expected_identity=agent_runtime_context.research_identity(context),
        require_calculator=has_legacy_calculation_marker
        or any(
            str(item.get("operation") or "")
            in {"normalize_amount", "yoy", "yoy_growth", "ratio", "cagr", "per_capita"}
            for item in trusted_calculation_runs
        ),
        require_reconciliation=has_legacy_reconciliation_marker or any(
            str(item.get("operation") or "") in {"goodwill_reconciliation", "gross_allowance_net_reconciliation"}
            for item in trusted_calculation_runs
        )
        or any(
            str(run.get("schema_version") or "") == "siq_financial_reconciliation_trace_v1"
            for run in structured_calculation_runs
        ),
        trusted_runs=trusted_calculation_runs,
        trusted_evidence=trusted_calculation_evidence,
    )
    if calculation_trace_validation.checked and not calculation_trace_validation.allowed:
        for run in calculator_runs:
            if run.get("source") in {"reply_structured", "backend_evidence_recompute"}:
                run["validated"] = False
                run["validation_reason"] = calculation_trace_validation.reason
    validated_calculation_lines = frozenset(
        int(run.get("display_line_number") or 0)
        for run in calculation_trace_validation.runs
        if str(run.get("trace_origin") or "") == "backend_evidence_recompute"
        and int(run.get("display_line_number") or 0) > 0
    )
    claim_verifier_reply = raw_reply if raw_reply is not None else final_reply
    claim_verifier_result = claim_verification_payload(
        verify_financial_claims(
            claim_verifier_reply or "",
            expected_identity=agent_runtime_context.research_identity(context),
            trusted_evidence=trusted_calculation_evidence,
            validated_calculation_lines=validated_calculation_lines,
        )
    )
    delivered_claim_verifier_result = claim_verification_payload(
        verify_financial_claims(
            final_reply or "",
            expected_identity=agent_runtime_context.research_identity(context),
            trusted_evidence=trusted_calculation_evidence,
            validated_calculation_lines=validated_calculation_lines,
        )
    )
    created_at_text = created_at.isoformat() if isinstance(created_at, datetime) else str(created_at or _utc_now_iso())
    payload: dict[str, Any] = {
        "schema_version": ANSWER_AUDIT_TRACE_SCHEMA,
        "created_at": created_at_text,
        "profile": str(redact_audit_value(profile or "", max_string_length=256)),
        "session_id": str(redact_audit_value(session_id or "", max_string_length=256)),
        "question_id": _extract_question_id(message or "", context),
        "message_hash": stable_hash(sanitized_message),
        "message_preview": _truncate_text(sanitized_message, 500),
        "answer_hash": stable_hash(sanitized_reply),
        "answer_preview": _truncate_text(sanitized_reply, 500),
        "resolved_company": _extract_resolved_company(context, references),
        "resolved_period": _extract_resolved_period(context, references),
        "query_plan": _extract_query_plan(context, references),
        "wiki_facts": wiki_facts,
        "postgres_facts": postgres_facts,
        "legal_facts": legal_facts,
        "fallback_reason": _extract_fallback_reason(context, references, final_reply or ""),
        "calculator_runs": calculator_runs,
        "calculation_trace_validation": {
            "checked": calculation_trace_validation.checked,
            "allowed": calculation_trace_validation.allowed,
            "reason": calculation_trace_validation.reason or None,
            "structured_run_count": len(calculation_trace_validation.runs),
        },
        "claim_verifier_result": claim_verifier_result,
        "delivered_claim_verifier_result": delivered_claim_verifier_result,
        "citations": references,
    }
    fallback_events = _find_context_value(context, {"auditfallbackevents", "fallbackevents", "postgresfallbackevents"})
    if fallback_events not in (None, "", [], {}):
        payload["fallback_events"] = redact_audit_value(fallback_events, max_string_length=1000)
    payload["guardrail_result"] = _build_guardrail_result(
        raw_reply=raw_reply,
        final_reply=final_reply or "",
        references=references,
        calculator_runs=calculator_runs,
        enforce_evidence_contract=enforce_evidence_contract,
        guardrail_result=guardrail_result,
    )
    return redact_audit_value(payload, max_string_length=1200)


def record_answer_audit_trace(
    record: Mapping[str, Any],
    *,
    log_path: str | Path | None = None,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    payload = _with_trace_id(record)
    RECENT_ANSWER_AUDIT_TRACES.append(payload)
    del RECENT_ANSWER_AUDIT_TRACES[:-RECENT_ANSWER_AUDIT_TRACE_LIMIT]
    try:
        observability.record_answer_audit_observation(payload)
    except Exception:
        pass

    path = Path(log_path).expanduser() if log_path is not None else _default_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    except OSError as exc:
        payload["audit_log_error"] = exc.__class__.__name__
        if raise_on_error:
            raise
    return payload


def _iter_answer_audit_trace_log(path: Path, *, max_lines: int = 20000) -> list[dict[str, Any]]:
    if not path.exists() or not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []

    records: list[dict[str, Any]] = []
    for line in reversed(lines[-max_lines:]):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, Mapping):
            records.append(_with_trace_id(payload))
    return records


def get_answer_audit_trace(
    trace_id: str,
    *,
    log_path: str | Path | None = None,
) -> dict[str, Any] | None:
    normalized = str(trace_id or "").strip()
    if not is_answer_audit_trace_id(normalized):
        return None
    for record in reversed(RECENT_ANSWER_AUDIT_TRACES):
        payload = _with_trace_id(record)
        if payload.get("trace_id") == normalized:
            return payload

    path = Path(log_path).expanduser() if log_path is not None else _default_log_path()
    for record in _iter_answer_audit_trace_log(path):
        if record.get("trace_id") == normalized:
            return record
    return None


def record_answer_audit_trace_for_reply(
    *,
    message: str,
    final_reply: str,
    context: Any | None = None,
    profile: Any | None = None,
    session_id: str | None = None,
    raw_reply: str | None = None,
    enforce_evidence_contract: bool = True,
    guardrail_result: Mapping[str, Any] | None = None,
    trusted_calculation_runs: Sequence[Mapping[str, Any]] = (),
    trusted_calculation_evidence: Sequence[Mapping[str, Any]] = (),
    log_path: str | Path | None = None,
    raise_on_error: bool = False,
) -> dict[str, Any]:
    try:
        record = build_answer_audit_trace(
            message=message,
            final_reply=final_reply,
            context=context,
            profile=profile,
            session_id=session_id,
            raw_reply=raw_reply,
            enforce_evidence_contract=enforce_evidence_contract,
            guardrail_result=guardrail_result,
            trusted_calculation_runs=trusted_calculation_runs,
            trusted_calculation_evidence=trusted_calculation_evidence,
        )
    except Exception as exc:
        if raise_on_error:
            raise
        record = {
            "schema_version": ANSWER_AUDIT_TRACE_SCHEMA,
            "created_at": _utc_now_iso(),
            "profile": str(redact_audit_value(profile or "", max_string_length=256)),
            "session_id": str(redact_audit_value(session_id or "", max_string_length=256)),
            "audit_build_error": exc.__class__.__name__,
        }
    return record_answer_audit_trace(record, log_path=log_path, raise_on_error=raise_on_error)


def _summary_text(value: Any, *, fallback: str = "none", limit: int = 160) -> str:
    if value in (None, "", [], {}):
        return fallback
    text = str(redact_audit_value(value, max_string_length=limit)).strip()
    return _truncate_text(text, limit) or fallback


def render_answer_audit_summary(record: Mapping[str, Any]) -> str:
    query_plan = record.get("query_plan") if isinstance(record.get("query_plan"), Mapping) else {}
    guardrail = record.get("guardrail_result") if isinstance(record.get("guardrail_result"), Mapping) else {}
    wiki_facts = record.get("wiki_facts") if isinstance(record.get("wiki_facts"), list) else []
    postgres_facts = record.get("postgres_facts") if isinstance(record.get("postgres_facts"), list) else []
    calculator_runs = record.get("calculator_runs") if isinstance(record.get("calculator_runs"), list) else []
    citations = record.get("citations") if isinstance(record.get("citations"), list) else []
    observed_sources = query_plan.get("observed_source_types") if isinstance(query_plan, Mapping) else []
    if isinstance(observed_sources, list):
        observed_sources_text = ", ".join(str(item) for item in observed_sources if str(item).strip())
    else:
        observed_sources_text = str(observed_sources or "")
    blocked = guardrail.get("blocked") if isinstance(guardrail, Mapping) else None
    guardrail_text = "blocked" if blocked is True else "passed"
    return "\n".join(
        [
            "## 审计详情",
            f"- trace_schema: `{_summary_text(record.get('schema_version'))}`",
            f"- trace_id: `{_summary_text(record.get('trace_id'), fallback=answer_audit_trace_id(record))}`",
            f"- question_id: `{_summary_text(record.get('question_id'), fallback='auto')}`",
            f"- source_counts: `wiki={len(wiki_facts)}, postgres={len(postgres_facts)}, citations={len(citations)}`",
            f"- fallback_reason: `{_summary_text(record.get('fallback_reason'))}`",
            f"- calculator_runs: `{len(calculator_runs)}`",
            f"- guardrail: `{guardrail_text}`",
            f"- observed_sources: `{_summary_text(observed_sources_text)}`",
        ]
    )


def append_answer_audit_summary(reply: str, record: Mapping[str, Any]) -> str:
    text = str(reply or "")
    if not text.strip() or _AUDIT_SUMMARY_HEADING_RE.search(text):
        return text
    summary = render_answer_audit_summary(record)
    return f"{text.rstrip()}\n\n{summary}"


__all__ = [
    "ANSWER_AUDIT_TRACE_SCHEMA",
    "ANSWER_AUDIT_TRACE_ID_PREFIX",
    "RECENT_ANSWER_AUDIT_TRACES",
    "append_answer_audit_summary",
    "answer_audit_trace_id",
    "build_answer_audit_trace",
    "get_answer_audit_trace",
    "is_answer_audit_trace_id",
    "record_answer_audit_trace",
    "record_answer_audit_trace_for_reply",
    "redact_audit_value",
    "render_answer_audit_summary",
    "stable_hash",
]
