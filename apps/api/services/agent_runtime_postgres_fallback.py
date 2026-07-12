"""Pure PostgreSQL fallback parse and predicate helpers for the agent runtime."""

from __future__ import annotations

import importlib
import inspect
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services import agent_runtime_context, market_document_identity


@dataclass(frozen=True)
class PostgresFallbackDependencies:
    should_consider_postgres_fallback: Callable[[str, Any | None], bool]
    record_postgres_fallback_event: Callable[..., None]
    load_financial_query_api: Callable[[], Any | None]
    postgres_query_text: Callable[[str, Any | None], str]
    postgres_prepare_parsed: Callable[[dict[str, Any], str], dict[str, Any]]
    postgres_market_agent_view_result: Callable[[Any, str, Any | None, dict[str, Any], str, int], dict[str, Any] | None]
    financial_query_connection_factory: Callable[[Any], Callable[[], Any] | None]
    postgres_requested_metric_terms: Callable[[str], list[str]]
    postgres_query_metric_rows: Callable[[Any, Any, dict[str, Any], dict[str, Any], str, int], tuple[list[str], list[dict[str, Any]]]]
    postgres_row_matches_requested_terms: Callable[[dict[str, Any], list[str]], bool]
    postgres_enrich_rows_with_table_pages: Callable[[Any, list[dict[str, Any]]], None]
    normalize_json: Callable[[Any, Any], Any]
    postgres_legacy_fallback_allowed: Callable[[Any | None], tuple[bool, str | None]] | None = None
    log_exception: Callable[[BaseException], None] | None = None


@dataclass(frozen=True)
class PostgresFallbackContextDependencies:
    record_postgres_fallback_event: Callable[..., None]
    audit_context_with_fallback_event: Callable[..., Any]
    postgres_fallback_result: Callable[[str, Any], dict[str, Any] | None]
    render_postgres_fallback_context: Callable[[dict[str, Any]], str]


def postgres_query_text(
    message: str,
    context: Any | None = None,
    *,
    context_company_hint: Callable[[Any | None], str],
) -> str:
    hint = context_company_hint(context)
    if not hint:
        return message
    return f"{message}\n\n当前页面公司提示：{hint}"


def load_financial_query_api(script_dir: str | Path, *, module_name: str = "financial_query_api") -> Any | None:
    script_path = str(script_dir)
    if script_path not in sys.path:
        sys.path.insert(0, script_path)
    try:
        return importlib.import_module(module_name)
    except Exception:
        return None


def financial_query_connection_factory(module: Any) -> Callable[[], Any] | None:
    get_connection = getattr(module, "get_connection", None)
    if callable(get_connection):
        return get_connection
    pg = getattr(module, "pg", None)
    get_connection = getattr(pg, "get_connection", None)
    if callable(get_connection):
        return get_connection
    return None


def _context_mapping(context: Any | None) -> dict[str, Any]:
    if hasattr(context, "model_dump"):
        raw = context.model_dump(exclude_none=True)
    elif isinstance(context, Mapping):
        raw = dict(context)
    else:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def _field_mapping(raw: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value in (None, ""):
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _market_from_identifier(value: Any) -> str | None:
    text = str(value or "").strip()
    if ":" not in text:
        return None
    prefix = text.split(":", 1)[0].upper()
    if prefix in {"HK", "JP", "KR", "EU", "US", "US_SEC"}:
        return market_document_identity.normalize_market_code(prefix)
    return None


def _company_mapping(
    context: Any | None,
    context_company: Callable[[Any | None], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if context_company is None:
        return _field_mapping(_context_mapping(context), "company")
    value = context_company(context)
    return dict(value) if isinstance(value, Mapping) else {}


def postgres_context_market(
    context: Any | None,
    *,
    context_company: Callable[[Any | None], dict[str, Any]] | None = None,
) -> str | None:
    raw = _context_mapping(context)
    identity = _field_mapping(raw, "research_identity")
    company = _company_mapping(context, context_company)
    report = _field_mapping(raw, "report")
    filing = _field_mapping(raw, "filing")
    resolved_period = _field_mapping(raw, "resolved_period")
    postgres = _field_mapping(raw, "postgres")
    filing_id = _first_text(
        identity.get("filing_id"),
        raw.get("filing_id"),
        postgres.get("filing_id"),
        resolved_period.get("filing_id"),
        report.get("filing_id"),
        filing.get("filing_id"),
        company.get("filing_id"),
    )
    company_id = _first_text(
        identity.get("company_id"),
        raw.get("company_id"),
        company.get("company_id"),
        company.get("id"),
        report.get("company_id"),
        filing.get("company_id"),
        postgres.get("company_id"),
    )
    market = _first_text(
        identity.get("market"),
        raw.get("market"),
        postgres.get("market"),
        company.get("market"),
        report.get("market"),
        filing.get("market"),
        resolved_period.get("market"),
        _market_from_identifier(filing_id),
        _market_from_identifier(company_id),
    )
    if not market:
        return None
    return market_document_identity.normalize_market_code(market)


def postgres_legacy_fallback_allowed(context: Any | None) -> tuple[bool, str | None]:
    market = postgres_context_market(context)
    if market in {"HK", "JP", "KR", "EU", "US"}:
        return False, market
    return True, market


def _accepts_market_kwarg(func: Callable[..., Any]) -> bool:
    try:
        parameters = inspect.signature(func).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(parameter.kind == inspect.Parameter.VAR_KEYWORD or parameter.name == "market" for parameter in parameters)


def postgres_agent_query_scope(
    context: Any | None,
    *,
    context_company: Callable[[Any | None], dict[str, Any]],
) -> dict[str, str]:
    raw = _context_mapping(context)
    identity = _field_mapping(raw, "research_identity")
    company = _company_mapping(context, context_company)
    report = _field_mapping(raw, "report")
    filing = _field_mapping(raw, "filing")
    resolved_period = _field_mapping(raw, "resolved_period")
    postgres = _field_mapping(raw, "postgres")

    parse_run_id = _first_text(
        identity.get("parse_run_id"),
        raw.get("parse_run_id"),
        raw.get("postgres_parse_run_id"),
        postgres.get("parse_run_id"),
        resolved_period.get("parse_run_id"),
        report.get("parse_run_id"),
        filing.get("parse_run_id"),
        company.get("parse_run_id"),
    )
    filing_id = _first_text(
        identity.get("filing_id"),
        raw.get("filing_id"),
        postgres.get("filing_id"),
        resolved_period.get("filing_id"),
        report.get("filing_id"),
        filing.get("filing_id"),
        company.get("filing_id"),
    )
    market = postgres_context_market(context, context_company=context_company)
    if not market:
        return {}
    identity = market_document_identity.MarketDocumentFullIdentity(
        market=market,
        parse_run_id=parse_run_id,
        filing_id=filing_id,
    )
    return market_document_identity.build_agent_query_scope(identity)


def should_consider_postgres_fallback(
    message: str | None,
    context: Any | None = None,
    *,
    is_general_assistant_request: Callable[[str], bool],
    is_human_capital_query: Callable[[str], bool],
    is_statement_query: Callable[[str], bool],
    should_inject_note_detail_context: Callable[[str], bool],
    postgres_fallback_terms: Sequence[str],
    context_company: Callable[[Any | None], dict[str, Any]],
) -> bool:
    text = re.sub(r"\s+", "", message or "")
    if not text or is_general_assistant_request(text):
        return False
    raw_message = message or ""
    if is_human_capital_query(raw_message):
        return False
    if is_statement_query(raw_message) or should_inject_note_detail_context(raw_message):
        return True
    if any(term.lower() in text.lower() for term in postgres_fallback_terms):
        return True
    company = context_company(context)
    return bool(company and any(term in text for term in ("多少", "数据", "情况", "如何", "怎么样")))


def postgres_prepare_parsed(parsed: dict[str, Any], message: str) -> dict[str, Any]:
    output = dict(parsed)
    if output.get("metric_name") or output.get("canonical_name") or output.get("statement_type"):
        return output
    text = re.sub(r"\s+", "", message or "")
    if any(term in text for term in ("财务", "业绩", "表现", "基本面", "经营情况", "主要数据", "核心数据", "数据", "情况")):
        output["query_type"] = "company_all"
    return output


def postgres_requested_metric_terms(
    message: str,
    *,
    financial_note_metric_terms: tuple[str, ...],
    core_key_metric_terms: tuple[str, ...],
    core_key_metric_aliases: dict[str, tuple[str, ...]],
) -> list[str]:
    text = re.sub(r"\s+", "", message or "").lower()
    if not text:
        return []
    terms: list[str] = []
    for term in (*financial_note_metric_terms, *core_key_metric_terms):
        if re.sub(r"\s+", "", str(term)).lower() in text:
            terms.append(str(term))
    for aliases in core_key_metric_aliases.values():
        if any(re.sub(r"\s+", "", str(alias)).lower() in text for alias in aliases):
            terms.extend(str(alias) for alias in aliases)
    return sorted(dict.fromkeys(term for term in terms if term), key=len, reverse=True)


def postgres_row_matches_requested_terms(
    row: dict[str, Any],
    requested_terms: list[str],
    *,
    normalize_financial_text: Callable[[Any], str],
    postgres_row_payload: Callable[[dict[str, Any]], dict[str, Any]],
) -> bool:
    if not requested_terms:
        return True
    payload = postgres_row_payload(row)
    row_text = normalize_financial_text(
        " ".join(
            str(value)
            for value in (
                row.get("item_name"),
                row.get("metric_name"),
                row.get("metric_key"),
                row.get("canonical_name"),
                payload.get("item_name"),
                payload.get("metric_name"),
                payload.get("metric_key"),
                payload.get("canonical_name"),
            )
            if value not in (None, "")
        )
    )
    return any(normalize_financial_text(term) in row_text for term in requested_terms)


def postgres_query_metric_rows(
    module: Any,
    cur: Any,
    parsed: dict[str, Any],
    company: dict[str, Any],
    query_text: str,
    limit: int,
) -> tuple[list[str], list[dict[str, Any]]]:
    module.infer_metric_from_database(cur, parsed, company, query_text)
    if parsed.get("query_type") == "table":
        return module.query_statement_table(cur, parsed, company, limit)
    source_tables, rows = module.query_metric_from_split_tables(cur, parsed, company, limit)
    if not rows:
        wide_tables, wide_rows = module.query_metric_from_wide(cur, parsed, company, limit)
        source_tables = list(dict.fromkeys([*source_tables, *wide_tables]))
        rows.extend(wide_rows)
    return source_tables, module.dedupe_response_rows(rows, limit)


def postgres_enrich_rows_with_table_pages(
    cur: Any,
    rows: list[dict[str, Any]],
    *,
    postgres_row_pdf_page: Callable[[dict[str, Any]], Any],
    postgres_row_table_index: Callable[[dict[str, Any]], Any],
) -> None:
    pairs: list[tuple[str, int]] = []
    seen_pairs: set[tuple[str, int]] = set()
    for row in rows:
        if postgres_row_pdf_page(row):
            continue
        task_id = str(row.get("task_id") or "").strip()
        table_index = postgres_row_table_index(row)
        if not task_id or table_index in (None, ""):
            continue
        try:
            pair = (task_id, int(table_index))
        except (TypeError, ValueError):
            continue
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            pairs.append(pair)
    if not pairs:
        return
    placeholders = ", ".join(["(%s, %s)"] * len(pairs))
    params: list[Any] = []
    for task_id, table_index in pairs:
        params.extend([task_id, table_index])
    try:
        cur.execute(
            f"""
            SELECT task_id, table_index, pdf_page_number, markdown_line
            FROM pdf2md.document_tables
            WHERE (task_id, table_index) IN ({placeholders})
            """,
            params,
        )
    except Exception:
        return
    table_pages = {
        (str(row.get("task_id")), int(row.get("table_index"))): dict(row)
        for row in cur.fetchall()
        if row.get("task_id") and row.get("table_index") is not None
    }
    for row in rows:
        task_id = str(row.get("task_id") or "").strip()
        table_index = postgres_row_table_index(row)
        try:
            key = (task_id, int(table_index))
        except (TypeError, ValueError):
            continue
        table = table_pages.get(key)
        if not table:
            continue
        if not postgres_row_pdf_page(row) and table.get("pdf_page_number"):
            row["source_page_number"] = table.get("pdf_page_number")
        if not row.get("source_markdown_line") and table.get("markdown_line"):
            row["source_markdown_line"] = table.get("markdown_line")


def postgres_market_agent_view_result(
    module: Any,
    message: str,
    context: Any | None,
    parsed: dict[str, Any],
    query_text: str,
    limit: int,
    *,
    context_company: Callable[[Any | None], dict[str, Any]],
    log_exception: Callable[[BaseException], None] | None = None,
) -> dict[str, Any] | None:
    query_market_agent_view_result = getattr(module, "query_market_agent_view_result", None)
    if not callable(query_market_agent_view_result):
        return None
    scope = postgres_agent_query_scope(context, context_company=context_company)
    scoped_parsed = {**parsed, **scope}
    kwargs: dict[str, Any] = {"limit": limit}
    if scope.get("market") and _accepts_market_kwarg(query_market_agent_view_result):
        kwargs["market"] = scope["market"]
    try:
        result = query_market_agent_view_result(
            query_text,
            scoped_parsed,
            context_company(context),
            **kwargs,
        )
    except Exception as exc:
        record_postgres_fallback_event(
            context,
            reason="postgres_unavailable",
            stage="market_agent_view_exception",
            detail=exc.__class__.__name__,
            source="postgres_market_view",
        )
        if log_exception:
            log_exception(exc)
        return None
    if not isinstance(result, dict) or not result.get("rows"):
        record_postgres_fallback_event(
            context,
            reason="market_view_miss",
            stage="market_agent_view_no_rows",
            source="postgres_market_view",
        )
        return None
    record_postgres_fallback_event(
        context,
        reason="market_view_hit",
        stage="market_agent_view_rows",
        source="postgres_market_view",
    )
    result.setdefault("question", message)
    result.setdefault("fallback_reason", "market_view_hit")
    return result


def postgres_fallback_result(
    message: str,
    context: Any | None = None,
    *,
    limit: int,
    deps: PostgresFallbackDependencies,
) -> dict[str, Any] | None:
    if not deps.should_consider_postgres_fallback(message, context):
        deps.record_postgres_fallback_event(
            context,
            reason="postgres_not_applicable",
            stage="should_consider_postgres_fallback_false",
        )
        return None
    deps.record_postgres_fallback_event(
        context,
        reason="wiki_structured_miss",
        stage="postgres_fallback_started",
        source="wiki_first",
    )
    market, missing_identity_fields = agent_runtime_context.incomplete_non_cn_research_identity(context)
    if market and missing_identity_fields:
        detail = f"market={market} missing={','.join(missing_identity_fields)}"
        deps.record_postgres_fallback_event(
            context,
            reason="research_identity_incomplete",
            stage="market_agent_view_skipped_for_incomplete_identity",
            detail=detail,
            source="research_identity_guard",
        )
        deps.record_postgres_fallback_event(
            context,
            reason="market_boundary_closed",
            stage="legacy_fallback_skipped_for_non_cn_market",
            detail=market,
            source="postgres_market_view",
        )
        return None
    module = deps.load_financial_query_api()
    if module is None:
        deps.record_postgres_fallback_event(
            context,
            reason="postgres_unavailable",
            stage="financial_query_api_import_failed",
        )
        return None
    query_text = deps.postgres_query_text(message, context)
    try:
        parsed = module.merge_parse(query_text, False)
        parsed = deps.postgres_prepare_parsed(parsed, message)
        market_result = deps.postgres_market_agent_view_result(module, message, context, parsed, query_text, limit)
        if market_result:
            return market_result
        legacy_allowed, market = (deps.postgres_legacy_fallback_allowed or postgres_legacy_fallback_allowed)(context)
        if not legacy_allowed:
            deps.record_postgres_fallback_event(
                context,
                reason="market_boundary_closed",
                stage="legacy_fallback_skipped_for_non_cn_market",
                detail=market,
                source="postgres_market_view",
            )
            return None
        get_connection = deps.financial_query_connection_factory(module)
        if get_connection is None:
            deps.record_postgres_fallback_event(
                context,
                reason="postgres_unavailable",
                stage="legacy_connection_factory_missing",
            )
            return None
        with get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SET TRANSACTION READ ONLY")
                except Exception:
                    try:
                        cur.execute("SET default_transaction_read_only = on")
                    except Exception:
                        pass
                company = module.resolve_company(cur, parsed, query_text)
                if not company:
                    deps.record_postgres_fallback_event(
                        context,
                        reason="postgres_company_miss",
                        stage="legacy_resolve_company_no_match",
                    )
                    return None
                parsed.update({f"resolved_{key}": value for key, value in company.items()})
                requested_terms = deps.postgres_requested_metric_terms(message)
                source_tables: list[str] = []
                rows: list[dict[str, Any]] = []
                if requested_terms:
                    metric_parsed = dict(parsed)
                    source_tables, rows = deps.postgres_query_metric_rows(
                        module,
                        cur,
                        metric_parsed,
                        company,
                        query_text,
                        limit,
                    )
                    if rows:
                        parsed = metric_parsed

                if not rows and parsed.get("query_type") == "company_all":
                    source_tables, rows = module.query_company_all_metrics(cur, parsed, company, limit)
                elif not rows:
                    source_tables, rows = deps.postgres_query_metric_rows(
                        module,
                        cur,
                        parsed,
                        company,
                        query_text,
                        limit,
                    )
                if requested_terms and rows and not any(
                    deps.postgres_row_matches_requested_terms(row, requested_terms) for row in rows
                ):
                    deps.record_postgres_fallback_event(
                        context,
                        reason="postgres_metric_miss",
                        stage="legacy_rows_do_not_match_requested_terms",
                    )
                    return None
                deps.postgres_enrich_rows_with_table_pages(cur, rows)
    except Exception as exc:
        deps.record_postgres_fallback_event(
            context,
            reason="postgres_unavailable",
            stage="legacy_postgres_exception",
            detail=exc.__class__.__name__,
        )
        if deps.log_exception:
            deps.log_exception(exc)
        return None
    if not rows:
        deps.record_postgres_fallback_event(
            context,
            reason="postgres_metric_miss",
            stage="legacy_no_rows",
        )
        return None
    deps.record_postgres_fallback_event(
        context,
        reason="postgres_hit",
        stage="legacy_rows",
    )
    return {
        "question": message,
        "query_text": query_text,
        "parsed": deps.normalize_json(module, parsed),
        "source_tables": source_tables,
        "rows": [deps.normalize_json(module, row) for row in rows[:limit]],
        "fallback_reason": "postgres_hit",
    }


def build_postgres_fallback_context(
    message: str,
    context: Any | None = None,
    *,
    deps: PostgresFallbackContextDependencies,
) -> str | None:
    if isinstance(context, dict):
        postgres_context = context
        deps.record_postgres_fallback_event(
            postgres_context,
            reason="wiki_fulltext_miss",
            stage="postgres_context_fallback_attempt",
            source="wiki_first",
        )
    else:
        postgres_context = deps.audit_context_with_fallback_event(
            context,
            reason="wiki_fulltext_miss",
            stage="postgres_context_fallback_attempt",
            source="wiki_first",
        )
    result = deps.postgres_fallback_result(message, postgres_context)
    if not result:
        return None
    return deps.render_postgres_fallback_context(result)


def audit_context_with_fallback_event(
    context: Any | None,
    *,
    reason: str,
    stage: str,
    detail: str | None = None,
    source: str = "postgres_fallback",
) -> Any:
    event = {
        "reason": reason,
        "stage": stage,
        "source": source,
    }
    if detail:
        event["detail"] = detail[:300]
    if isinstance(context, dict):
        output = dict(context)
        events = output.get("_audit_fallback_events")
        output["_audit_fallback_events"] = [
            *(events if isinstance(events, list) else []),
            event,
        ]
        output.setdefault("fallback_reason", reason)
        return output
    return {
        "original_context_type": type(context).__name__ if context is not None else None,
        "_audit_fallback_events": [event],
        "fallback_reason": reason,
    }


def context_fallback_events(context: Any | None) -> list[dict[str, Any]]:
    if not isinstance(context, dict):
        return []
    events = context.get("_audit_fallback_events")
    return [item for item in events if isinstance(item, dict)] if isinstance(events, list) else []


def record_postgres_fallback_event(
    context: Any | None,
    *,
    reason: str,
    stage: str,
    detail: str | None = None,
    source: str = "postgres_fallback",
) -> None:
    if not isinstance(context, dict):
        return
    event = {
        "reason": reason,
        "stage": stage,
        "source": source,
    }
    if detail:
        event["detail"] = detail[:300]
    context.setdefault("_audit_fallback_events", []).append(event)
    context.setdefault("fallback_reason", reason)


def audit_context_for_final_reply(context: Any | None, reply: str) -> Any:
    events = context_fallback_events(context)
    if not events:
        return context
    postgres_markers = ("source_type=postgres", "source_type=postgresql", "PostgreSQL fallback", "数据库 fallback")
    if any(marker in (reply or "") for marker in postgres_markers):
        return context
    return audit_context_with_fallback_event(
        context,
        reason="wiki_structured_miss",
        stage="runtime_answer_no_postgres_citation",
        source="wiki_first",
    )


__all__ = [
    "build_postgres_fallback_context",
    "audit_context_for_final_reply",
    "audit_context_with_fallback_event",
    "context_fallback_events",
    "financial_query_connection_factory",
    "load_financial_query_api",
    "postgres_enrich_rows_with_table_pages",
    "postgres_fallback_result",
    "postgres_agent_query_scope",
    "postgres_context_market",
    "postgres_legacy_fallback_allowed",
    "postgres_market_agent_view_result",
    "postgres_prepare_parsed",
    "postgres_query_metric_rows",
    "postgres_query_text",
    "postgres_requested_metric_terms",
    "postgres_row_matches_requested_terms",
    "record_postgres_fallback_event",
    "PostgresFallbackContextDependencies",
    "PostgresFallbackDependencies",
    "should_consider_postgres_fallback",
]
