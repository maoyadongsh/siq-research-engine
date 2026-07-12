from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from services import market_document_identity, market_report_commands, market_report_status_service


class MarketReportPostgresError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _document_full_status_selectors(
    *,
    parse_run_id: str | None,
    filing_id: str | None,
    document_full_path: str | None,
    task_id: str | None,
) -> dict[str, str]:
    raw = {
        "parse_run_id": parse_run_id,
        "filing_id": filing_id,
        "document_full_path": document_full_path,
        "task_id": task_id,
    }
    return {
        key: str(value).strip()
        for key, value in raw.items()
        if value is not None and str(value).strip()
    }


def _validate_document_full_status_selectors(
    *,
    market: str | None,
    parse_run_id: str | None,
    filing_id: str | None,
    document_full_path: str | None,
    task_id: str | None,
) -> dict[str, str]:
    selectors = _document_full_status_selectors(
        parse_run_id=parse_run_id,
        filing_id=filing_id,
        document_full_path=document_full_path,
        task_id=task_id,
    )
    if len(selectors) > 1:
        names = ", ".join(selectors)
        raise MarketReportPostgresError(
            400,
            f"document_full status selectors are mutually exclusive: {names}",
        )
    filing_market = market_document_identity.market_from_identifier(selectors.get("filing_id"))
    requested_market = market_document_identity.normalize_market_code(market)
    if requested_market and filing_market and requested_market != filing_market:
        raise MarketReportPostgresError(
            400,
            f"market {requested_market} conflicts with filing_id market {filing_market}",
        )
    return selectors


def run_market_document_full_import(
    *,
    payload: Mapping[str, Any],
    market: str,
    executable: str,
    repo_root: Path,
    market_document_full_import_scripts: Mapping[str, Path],
    market_document_full_roots: Mapping[str, Path],
    market_databases: Mapping[str, str],
    safe_market_document_full_path: Callable[[str, str], Path],
    run_command: Callable[..., Any],
    command_for_display: Callable[[list[str]], str],
    record_pipeline_failure: Callable[..., None],
    record_ingestion_duration: Callable[..., None],
    base_env: Mapping[str, str] | None = None,
    timeout: int = 900,
) -> dict[str, Any]:
    started = time.perf_counter()
    metric_status = "failure"
    try:
        plan = market_report_commands.build_market_document_full_import_plan(
            payload=payload,
            market=market,
            market_document_full_import_scripts=market_document_full_import_scripts,
            safe_market_document_full_path=safe_market_document_full_path,
            repo_root=repo_root,
            market_document_full_roots=market_document_full_roots,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        record_pipeline_failure(
            market=market,
            action="postgres",
            reason=f"plan_error_{exc.status_code}",
        )
        record_ingestion_duration(
            market=market,
            stage="postgres_import",
            status=metric_status,
            duration_seconds=time.perf_counter() - started,
        )
        raise MarketReportPostgresError(exc.status_code, exc.detail) from exc

    try:
        args = market_report_commands.market_document_full_import_args(
            executable=executable,
            script=plan.script,
            market=plan.market,
            document_full_path=plan.document_full_path,
            payload=payload,
        )
        import_env = market_report_commands.market_document_full_import_env(
            market,
            market_databases,
            base_env=base_env,
            database_url=str(payload.get("database_url") or "").strip() or None,
        )
        run_kwargs: dict[str, Any] = {"cwd": repo_root, "timeout": timeout}
        if import_env:
            run_kwargs["env"] = import_env
        completed = run_command(args, **run_kwargs)
        result = market_report_commands.market_document_full_import_result_payload(
            completed=completed,
            command=command_for_display(args),
        )
        result["selector"] = dict(plan.selector)
        result["identity"] = {
            "market": plan.identity.market,
            **plan.identity.selector_payload(),
            "path_keys": list(plan.identity.path_keys),
        }
        metric_status = "success" if result.get("ok") else "failure"
        if not result.get("ok"):
            record_pipeline_failure(
                market=plan.market,
                action="postgres",
                reason=f"returncode_{result.get('returncode')}",
            )
        return result
    except Exception:
        record_pipeline_failure(
            market=market,
            action="postgres",
            reason="exception",
        )
        raise
    finally:
        record_ingestion_duration(
            market=market,
            stage="postgres_import",
            status=metric_status,
            duration_seconds=time.perf_counter() - started,
        )


def market_document_full_import_status(
    *,
    market: str | None,
    parse_run_id: str | None,
    filing_id: str | None,
    document_full_path: str | None,
    task_id: str | None,
    markets_to_search: Callable[[str | None], list[str]],
    document_full_path_keys: Callable[[str, str | None], list[str]],
    document_full_roots: Mapping[str, Path],
    import_scripts: Mapping[str, Path],
    market_databases: Mapping[str, str],
    schemas: Mapping[str, str],
    rel_or_abs: Callable[[Path], str],
    db_status_for_market: Callable[..., dict[str, Any]],
    record_fact_counts: Callable[[str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    _validate_document_full_status_selectors(
        market=market,
        parse_run_id=parse_run_id,
        filing_id=filing_id,
        document_full_path=document_full_path,
        task_id=task_id,
    )
    if document_full_path and not market:
        raise MarketReportPostgresError(400, "market is required when document_full_path is provided")
    codes = markets_to_search(market)
    if document_full_path and market:
        for code in codes:
            document_full_path_keys(code, document_full_path)
    return market_report_status_service.market_document_full_status_payload(
        market_codes=codes,
        document_full_roots=dict(document_full_roots),
        import_scripts=dict(import_scripts),
        market_databases=dict(market_databases),
        schemas=dict(schemas),
        rel_or_abs=rel_or_abs,
        db_status_for_market=lambda code: db_status_for_market(
            code,
            parse_run_id=parse_run_id,
            filing_id=filing_id,
            document_full_path=document_full_path,
            task_id=task_id,
        ),
        record_fact_counts=record_fact_counts,
    )
