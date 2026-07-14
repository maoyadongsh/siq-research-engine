from __future__ import annotations

import logging
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from services import market_report_commands, market_report_status_service


class MarketReportPackageError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def truthy_payload_flag(payload: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, bool):
            if value:
                return True
            continue
        if str(value or "").strip().lower() in {"1", "true", "yes", "on"}:
            return True
    return False


def require_legacy_market_package_import(payload: Mapping[str, Any]) -> None:
    if truthy_payload_flag(payload, "legacy_package_import", "legacyPackageImport"):
        return
    raise market_report_commands.MarketPackagePlanError(
        422,
        (
            "Package PostgreSQL import is legacy-only. "
            "Use /market-reports/document-full/import, or pass legacy_package_import=true "
            "for an explicit compatibility import."
        ),
    )


def _log_force_audit(
    *,
    logger: logging.Logger,
    action: str,
    package_dir: Path,
    gates: dict[str, Any],
    audit: dict[str, str | None],
    blocked: bool,
    rel_or_abs: Callable[[Path], str],
) -> None:
    redact = market_report_commands.redact_audit_text
    logger.info(
        (
            "market package force requested action=%s package=%s operator=%s ticket=%s "
            "reason=%s expires_at=%s one_shot=%s blocked=%s force_allowed=%s "
            "hard_gate_rule_ids=%s soft_gate_rule_ids=%s"
        ),
        action,
        redact(rel_or_abs(package_dir)),
        redact(audit.get("operator")),
        redact(audit.get("ticket")),
        redact(audit.get("reason")),
        redact(audit.get("expires_at")),
        redact(audit.get("one_shot")),
        blocked,
        bool(gates.get("force_allowed")),
        gates.get("hard_gate_rule_ids") or [],
        gates.get("soft_gate_rule_ids") or [],
    )


def enforce_market_package_quality_gate(
    *,
    package_dir: Path,
    payload: Mapping[str, Any],
    action: str,
    quality_gates_with_load_plan: Callable[[Path], dict[str, Any]],
    rel_or_abs: Callable[[Path], str],
    logger: logging.Logger,
) -> None:
    gates = quality_gates_with_load_plan(package_dir)
    decision = market_report_commands.market_package_quality_gate_decision(
        gates=gates,
        payload=payload,
        action=action,
    )
    if decision.audit is not None:
        _log_force_audit(
            logger=logger,
            action=action,
            package_dir=package_dir,
            gates=gates,
            audit=decision.audit,
            blocked=decision.blocked,
            rel_or_abs=rel_or_abs,
        )
    if decision.error_status_code is not None:
        raise market_report_commands.MarketPackagePlanError(
            decision.error_status_code,
            decision.error_detail,
        )


def load_plan_for_package(
    package_dir: Path,
    *,
    read_json_file: Callable[[Path, Any], Any],
) -> dict[str, Any]:
    payload = read_json_file(package_dir / "metrics" / "load_plan.json", {})
    return payload if isinstance(payload, dict) else {}


def quality_gates_with_load_plan(
    package_dir: Path,
    *,
    quality_gates_for_package: Callable[[Path], dict[str, Any]],
    load_plan_for_package: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    gates = quality_gates_for_package(package_dir)
    return market_report_status_service.merge_load_plan_decision_into_gates(
        gates,
        load_plan_for_package(package_dir),
    )


def market_package_list_payload(
    *,
    market: str | None,
    query: str,
    limit: int,
    market_wiki_roots: Mapping[str, Path],
    markets_to_search: Callable[[str | None], list[str]],
    iter_market_packages: Callable[[str], list[Path]],
    read_market_package_summary: Callable[[Path], dict[str, Any]],
    rel_or_abs: Callable[[Path], str],
) -> dict[str, Any]:
    codes = markets_to_search(market)
    package_summaries: list[dict[str, Any]] = []
    for code in codes:
        for package_dir in iter_market_packages(code):
            package_summaries.append(read_market_package_summary(package_dir))
    return market_report_status_service.market_package_list_payload(
        market_codes=codes,
        package_summaries=package_summaries,
        roots={code: rel_or_abs(market_wiki_roots[code]) for code in codes},
        query=query,
        limit=limit,
    )


def market_package_detail_by_path_payload(
    *,
    market: str,
    package_path: str,
    market_code: Callable[[str | None], str],
    safe_market_package_path: Callable[[str, str | None], Path],
    read_market_package_detail: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    code = market_code(market)
    return read_market_package_detail(safe_market_package_path(code, package_path))


def market_package_detail_by_filing_id_payload(
    *,
    filing_id: str,
    market: str | None,
    find_market_package_by_filing_id: Callable[[str, str | None], tuple[str, Path]],
    read_market_package_detail: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    _code, package_dir = find_market_package_by_filing_id(filing_id, market)
    return read_market_package_detail(package_dir)


def market_package_quality_payload_for_path(
    *,
    market: str,
    package_path: str,
    market_code: Callable[[str | None], str],
    safe_market_package_path: Callable[[str, str | None], Path],
    rel_or_abs: Callable[[Path], str],
    read_json_file: Callable[[Path, Any], Any],
    load_plan_for_package: Callable[[Path], dict[str, Any]],
    quality_gates_with_load_plan: Callable[[Path], dict[str, Any]],
    include_source_map_summary: bool = True,
) -> dict[str, Any]:
    code = market_code(market)
    package_dir = safe_market_package_path(code, package_path)
    return market_report_status_service.market_package_quality_response(
        package_dir,
        rel_or_abs=rel_or_abs,
        read_json_file=read_json_file,
        load_plan_for_package=load_plan_for_package,
        quality_gates_with_load_plan=quality_gates_with_load_plan,
        include_source_map_summary=include_source_map_summary,
    )


def market_package_quality_payload_for_filing_id(
    *,
    filing_id: str,
    market: str | None,
    find_market_package_by_filing_id: Callable[[str, str | None], tuple[str, Path]],
    rel_or_abs: Callable[[Path], str],
    read_json_file: Callable[[Path, Any], Any],
    load_plan_for_package: Callable[[Path], dict[str, Any]],
    quality_gates_with_load_plan: Callable[[Path], dict[str, Any]],
    include_source_map_summary: bool = False,
) -> dict[str, Any]:
    _code, package_dir = find_market_package_by_filing_id(filing_id, market)
    return market_report_status_service.market_package_quality_response(
        package_dir,
        rel_or_abs=rel_or_abs,
        read_json_file=read_json_file,
        load_plan_for_package=load_plan_for_package,
        quality_gates_with_load_plan=quality_gates_with_load_plan,
        include_source_map_summary=include_source_map_summary,
    )


def package_file_target(
    *,
    package_dir: Path,
    file_path: str,
    safe_under: Callable[[Path, Path], Path],
) -> Path:
    if not file_path or file_path.startswith("/") or ".." in Path(file_path).parts:
        raise MarketReportPackageError(400, "Invalid file path")
    target = safe_under(package_dir, package_dir / file_path)
    if not target.is_file():
        raise MarketReportPackageError(404, "Package file not found")
    return target


def market_package_file_target(
    *,
    market: str,
    package_path: str,
    file_path: str,
    market_code: Callable[[str | None], str],
    safe_market_package_path: Callable[[str, str | None], Path],
    safe_under: Callable[[Path, Path], Path],
) -> Path:
    code = market_code(market)
    package_dir = safe_market_package_path(code, package_path)
    return package_file_target(
        package_dir=package_dir,
        file_path=file_path,
        safe_under=safe_under,
    )


def market_evidence_detail_payload(
    *,
    evidence_id: str,
    market: str | None,
    package_path: str | None,
    market_code: Callable[[str | None], str],
    safe_market_package_path: Callable[[str, str | None], Path],
    find_market_evidence: Callable[..., tuple[str, Path, dict[str, Any]]],
    rel_or_abs: Callable[[Path], str],
) -> dict[str, Any]:
    package_dir = safe_market_package_path(market_code(market), package_path) if market and package_path else None
    code, found_package, entry = find_market_evidence(evidence_id, market=market, package_dir=package_dir)
    file_path = entry.get("local_path")
    file_url = None
    if file_path:
        file_url = "/api/market-reports/package-file?" + urlencode({
            "market": code,
            "package_path": rel_or_abs(found_package),
            "file": str(file_path),
        })
    return {
        "ok": True,
        "market": code,
        "package_path": rel_or_abs(found_package),
        "evidence": entry,
        "file_url": file_url,
    }


def latest_us_sec_case_item_for_ticker(
    ticker: str,
    *,
    case_set_path: Path,
    read_json_file: Callable[[Path, Any], Any],
) -> dict[str, Any] | None:
    case_set = read_json_file(case_set_path, {})
    return market_report_status_service.latest_case_item_for_ticker(case_set, ticker)


def package_from_us_sec_selector(
    payload: Mapping[str, Any],
    *,
    latest_case_item_for_ticker: Callable[[str], dict[str, Any] | None],
    safe_package_path: Callable[[str | None], Path],
) -> Path:
    if payload.get("package_path"):
        return safe_package_path(str(payload.get("package_path")))
    ticker = str(payload.get("ticker") or "").strip().upper()
    if not ticker:
        raise MarketReportPackageError(400, "ticker or package_path is required")
    item = latest_case_item_for_ticker(ticker)
    if not item:
        raise MarketReportPackageError(404, f"No package for ticker {ticker}")
    return safe_package_path(str(item.get("package_path") or ""))


def us_sec_semantic_status_for_case_item(
    item: Mapping[str, Any],
    *,
    safe_package_path: Callable[[str | None], Path],
    read_json_file: Callable[[Path, Any], Any],
) -> dict[str, Any]:
    try:
        package_path = str(item.get("package_path") or "")
        if not package_path:
            return {}
        return market_report_status_service.us_sec_semantic_status_for_package(
            safe_package_path(package_path),
            read_json_file=read_json_file,
        )
    except Exception:
        return {}


def us_sec_case_set_status_payload(
    *,
    case_set_path: Path,
    ingest_report_path: Path,
    read_json_file: Callable[[Path, Any], Any],
    semantic_status_for_item: Callable[[dict[str, Any]], dict[str, Any]],
) -> dict[str, Any]:
    case_set = read_json_file(case_set_path, {})
    ingest_report = read_json_file(ingest_report_path, {})
    return market_report_status_service.us_sec_case_set_status_payload(
        case_set=case_set,
        ingest_report=ingest_report,
        case_set_path=str(case_set_path),
        ingest_report_path=str(ingest_report_path),
        semantic_status_for_item=semantic_status_for_item,
    )


def us_sec_package_detail_by_ticker_payload(
    ticker: str,
    *,
    latest_case_item_for_ticker: Callable[[str], dict[str, Any] | None],
    safe_package_path: Callable[[str | None], Path],
    read_package_detail: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    item = latest_case_item_for_ticker(ticker)
    if not item:
        raise MarketReportPackageError(404, f"No package for ticker {ticker}")
    return read_package_detail(safe_package_path(str(item.get("package_path") or "")))


def us_sec_package_detail_by_path_payload(
    package_path: str | None,
    *,
    safe_package_path: Callable[[str | None], Path],
    read_package_detail: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    return read_package_detail(safe_package_path(package_path))


def run_market_package_build(
    payload: Mapping[str, Any],
    *,
    executable: str,
    repo_root: Path,
    market: str,
    market_wiki_roots: Mapping[str, Path],
    market_build_scripts: Mapping[str, Path],
    eu_esef_package_build_script: Path,
    safe_download_path: Callable[[str], Path],
    adjacent_metadata_path: Callable[[Path], Path | None],
    run_command: Callable[..., Any],
    command_for_display: Callable[[list[str]], str],
    read_market_package_detail: Callable[[Path], dict[str, Any]],
    read_us_sec_package_detail: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    plan = market_report_commands.build_market_package_build_plan(
        payload=payload,
        market=market,
        repo_root=repo_root,
        market_wiki_roots=market_wiki_roots,
        market_build_scripts=market_build_scripts,
        eu_esef_package_build_script=eu_esef_package_build_script,
        safe_download_path=safe_download_path,
        adjacent_metadata_path=adjacent_metadata_path,
    )
    args = market_report_commands.market_package_build_args(
        executable=executable,
        script=plan.script,
        source_path=plan.source_path,
        output_root=plan.output_root,
        metadata_path=plan.metadata_path,
        parser_result_path=plan.parser_result_path,
        force=plan.force,
    )
    completed = run_command(args, cwd=repo_root, timeout=900)
    output_lines = (getattr(completed, "stdout", "") or "").strip().splitlines()
    detail = None
    if getattr(completed, "returncode", 1) == 0 and output_lines:
        package_path = Path(output_lines[-1])
        detail = (
            read_us_sec_package_detail(package_path)
            if plan.market == "US"
            else read_market_package_detail(package_path)
        )
    return market_report_commands.market_package_build_result_payload(
        completed=completed,
        package=detail,
        command=command_for_display(args),
    )


def run_market_package_import(
    payload: Mapping[str, Any],
    *,
    executable: str,
    repo_root: Path,
    market: str,
    market_databases: Mapping[str, str],
    market_import_scripts: Mapping[str, Path],
    safe_market_package_path: Callable[[str, str], Path],
    quality_gates_with_load_plan: Callable[[Path], dict[str, Any]],
    run_command: Callable[..., Any],
    command_for_display: Callable[[list[str]], str],
    rel_or_abs: Callable[[Path], str],
    logger: logging.Logger,
    base_env: Mapping[str, str],
) -> dict[str, Any]:
    require_legacy_market_package_import(payload)
    plan = market_report_commands.build_market_package_import_plan(
        payload=payload,
        market=market,
        market_import_scripts=market_import_scripts,
        safe_market_package_path=safe_market_package_path,
    )
    enforce_market_package_quality_gate(
        package_dir=plan.package_dir,
        payload=payload,
        action="import",
        quality_gates_with_load_plan=quality_gates_with_load_plan,
        rel_or_abs=rel_or_abs,
        logger=logger,
    )
    args = market_report_commands.market_package_import_args(
        executable=executable,
        script=plan.script,
        market=plan.market,
        package_dir=plan.package_dir,
        payload=dict(payload),
    )
    run_kwargs: dict[str, Any] = {"cwd": repo_root, "timeout": 900}
    import_env = market_report_commands.market_package_import_env(
        plan.market,
        market_databases,
        base_env=base_env,
        database_url=payload.get("database_url"),
    )
    if import_env:
        run_kwargs["env"] = import_env
    completed = run_command(args, **run_kwargs)
    return market_report_commands.market_package_import_result_payload(
        completed=completed,
        command=command_for_display(args),
    )


def run_market_vector_ingest(
    payload: Mapping[str, Any],
    *,
    executable: str,
    repo_root: Path,
    market: str,
    market_vector_collections: Mapping[str, str],
    vector_ingest_script: Path,
    safe_market_package_path: Callable[[str, str], Path],
    quality_gates_with_load_plan: Callable[[Path], dict[str, Any]],
    run_command: Callable[..., Any],
    command_for_display: Callable[[list[str]], str],
    rel_or_abs: Callable[[Path], str],
    logger: logging.Logger,
) -> dict[str, Any]:
    plan = market_report_commands.build_market_vector_ingest_plan(
        payload=payload,
        market=market,
        vector_ingest_script=vector_ingest_script,
        safe_market_package_path=safe_market_package_path,
    )
    enforce_market_package_quality_gate(
        package_dir=plan.package_dir,
        payload=payload,
        action="vector_ingest",
        quality_gates_with_load_plan=quality_gates_with_load_plan,
        rel_or_abs=rel_or_abs,
        logger=logger,
    )
    args, _dry_run = market_report_commands.market_vector_ingest_args(
        executable=executable,
        script=plan.script,
        package_dir=plan.package_dir,
        payload=dict(payload),
        market=plan.market,
        market_vector_collections=market_vector_collections,
    )
    completed = run_command(args, cwd=repo_root, timeout=1800)
    return market_report_commands.market_vector_ingest_result_payload(
        completed=completed,
        dry_run=plan.dry_run,
        command=command_for_display(args),
    )


def us_sec_ingest_args_for_payload(
    payload: Mapping[str, Any],
    *,
    executable: str,
    ingest_script: Path,
    case_set_path: Path,
    report_path: Path,
) -> list[str]:
    tickers, batch_tag = market_report_commands.normalize_us_sec_ingest_filters(payload)
    return market_report_commands.us_sec_ingest_args(
        executable=executable,
        script=ingest_script,
        case_set_path=case_set_path,
        report_path=report_path,
        payload={**dict(payload), "milvus": False},
        tickers=tickers,
        batch_tag=batch_tag,
    )


def us_sec_company_dirs_from_payload(
    payload: Mapping[str, Any],
    *,
    latest_case_item: Callable[[str], Mapping[str, Any] | None],
    safe_package_path: Callable[[str], Path],
) -> list[str]:
    tickers = str(payload.get("tickers") or "").strip().upper()
    values = [item for item in re.split(r"[,\s]+", tickers) if item] if tickers else []
    if not values and payload.get("ticker"):
        values = [str(payload.get("ticker") or "").strip().upper()]
    company_dirs: list[str] = []
    for ticker in values:
        item = latest_case_item(ticker)
        package_path = str((item or {}).get("package_path") or "")
        if not package_path:
            continue
        try:
            package_dir = safe_package_path(package_path)
        except Exception:
            continue
        if package_dir.parent.name == "reports":
            company_dirs.append(package_dir.parent.parent.name)
    return sorted(set(company_dirs))


def run_us_sec_semantic_prestep(
    payload: Mapping[str, Any],
    *,
    executable: str,
    repo_root: Path,
    rule_semantic_script: Path,
    llm_semantic_script: Path,
    company_dirs_from_payload: Callable[[dict[str, Any]], list[str]],
    llm_semantic_env: Callable[[], Mapping[str, str]],
    run_command: Callable[..., Any],
    command_for_display: Callable[[list[str]], str],
) -> list[dict[str, Any]]:
    semantic_requested = bool(payload.get("semantic") or payload.get("llm_semantic") or payload.get("wiki_semantic"))
    if not semantic_requested or payload.get("dry_run", True):
        return []
    if not rule_semantic_script.is_file() or not llm_semantic_script.is_file():
        return [{
            "stage": "us_sec_semantic",
            "status": "skipped",
            "reason": "market semantic scripts missing",
        }]
    company_dirs = company_dirs_from_payload(dict(payload))
    if not company_dirs:
        return [{
            "stage": "us_sec_semantic",
            "status": "skipped",
            "reason": "no company dirs resolved from payload",
        }]
    results = []
    for company_dir in company_dirs:
        rule_args = [
            executable,
            str(rule_semantic_script),
            "--market",
            "US",
            "--company",
            company_dir,
            "--skip-existing",
        ]
        llm_args = [
            executable,
            str(llm_semantic_script),
            "--market",
            "US",
            "--company",
            company_dir,
            "--skip-existing",
            "--allow-failures",
        ]
        rule_completed = run_command(rule_args, cwd=repo_root, timeout=900)
        llm_completed = run_command(llm_args, cwd=repo_root, timeout=1800, env=dict(llm_semantic_env()))
        results.append({
            "companyDir": company_dir,
            "rule": {
                "returncode": getattr(rule_completed, "returncode", 1),
                "stdout": (getattr(rule_completed, "stdout", "") or "")[-4000:],
                "stderr": (getattr(rule_completed, "stderr", "") or "")[-4000:],
                "command": command_for_display(rule_args),
            },
            "llm": {
                "returncode": getattr(llm_completed, "returncode", 1),
                "stdout": (getattr(llm_completed, "stdout", "") or "")[-4000:],
                "stderr": (getattr(llm_completed, "stderr", "") or "")[-4000:],
                "command": command_for_display(llm_args),
            },
        })
    return results


def _us_sec_semantic_only_result(
    *,
    semantic_prestep: list[dict[str, Any]],
    report: Any,
) -> dict[str, Any]:
    failed = [
        item for item in semantic_prestep
        if (item.get("rule") or {}).get("returncode") not in (None, 0)
        or (item.get("llm") or {}).get("returncode") not in (None, 0)
    ]
    return {
        "ok": not failed,
        "returnCode": 1 if failed else 0,
        "stdout": "US SEC Wiki semantic enhancement completed\n" if not failed else "",
        "stderr": "\n".join(
            part
            for item in failed
            for part in ((item.get("rule") or {}).get("stderr"), (item.get("llm") or {}).get("stderr"))
            if part
        ),
        "report": report,
        "command": "semantic-only",
        "semantic_only": True,
        "semantic_prestep": semantic_prestep,
    }


def run_us_sec_case_set_ingest(
    payload: Mapping[str, Any],
    *,
    executable: str,
    repo_root: Path,
    ingest_script: Path,
    case_set_path: Path,
    report_path: Path,
    semantic_prestep: Callable[[dict[str, Any]], list[dict[str, Any]]],
    run_command: Callable[..., Any],
    command_for_display: Callable[[list[str]], str],
    read_json_file: Callable[[Path, Any], Any],
) -> dict[str, Any]:
    payload_dict = dict(payload)
    semantic_results = semantic_prestep(payload_dict)
    semantic_requested = bool(
        payload_dict.get("semantic")
        or payload_dict.get("llm_semantic")
        or payload_dict.get("wiki_semantic")
    )
    semantic_only = (
        semantic_requested
        and not truthy_payload_flag(payload_dict, "postgres")
        and not truthy_payload_flag(payload_dict, "ddl")
        and not truthy_payload_flag(payload_dict, "legacy_package_import", "legacyPackageImport")
    )
    if semantic_only:
        return _us_sec_semantic_only_result(
            semantic_prestep=semantic_results,
            report=read_json_file(report_path, {}),
        )
    if not ingest_script.is_file():
        raise MarketReportPackageError(404, f"Missing ingest script: {ingest_script}")
    args = us_sec_ingest_args_for_payload(
        payload_dict,
        executable=executable,
        ingest_script=ingest_script,
        case_set_path=case_set_path,
        report_path=report_path,
    )
    try:
        completed = run_command(args, cwd=repo_root, timeout=1800)
    except subprocess.TimeoutExpired as exc:
        raise MarketReportPackageError(504, f"US SEC ingest timed out: {exc}") from exc
    report = read_json_file(report_path, {})
    result = market_report_commands.us_sec_case_set_ingest_result_payload(
        completed=completed,
        report=report,
        command=command_for_display(args),
    )
    result["semantic_prestep"] = semantic_results
    return result


def run_us_sec_rebuild_package(
    ticker: str,
    payload: Mapping[str, Any],
    *,
    executable: str,
    repo_root: Path,
    latest_case_item: Callable[[str], Mapping[str, Any] | None],
    safe_package_path: Callable[[str], Path],
    read_json_file: Callable[[Path, Any], Any],
    safe_under: Callable[[Path, Path], Path],
    package_build_script: Path,
    output_root: Path,
    run_command: Callable[..., Any],
    read_package_detail: Callable[[Path], dict[str, Any]],
) -> dict[str, Any]:
    del payload  # rebuild currently always uses the latest package source and forces regeneration.
    plan = market_report_commands.build_us_sec_rebuild_package_plan(
        ticker=ticker,
        latest_case_item=latest_case_item,
        safe_package_path=safe_package_path,
        read_json_file=read_json_file,
        safe_under=safe_under,
        package_build_script=package_build_script,
        output_root=output_root,
    )
    with tempfile.TemporaryDirectory(prefix="siq-sec-rebuild-") as tmp_dir:
        tmp_source = Path(tmp_dir) / "filing.htm"
        tmp_source.write_bytes(plan.source_path.read_bytes())
        tmp_metadata = None
        if plan.metadata_path is not None:
            tmp_metadata = Path(tmp_dir) / "filing.metadata.json"
            tmp_metadata.write_bytes(plan.metadata_path.read_bytes())
        args = market_report_commands.us_sec_rebuild_package_args(
            executable=executable,
            script=plan.script,
            source_path=tmp_source,
            output_root=plan.output_root,
            metadata_path=tmp_metadata,
            force=True,
        )
        try:
            completed = run_command(args, cwd=repo_root, timeout=900)
        except subprocess.TimeoutExpired as exc:
            raise MarketReportPackageError(504, f"US SEC package rebuild timed out: {exc}") from exc
    if getattr(completed, "returncode", 1) != 0:
        error_output = (getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "")[-2000:]
        raise MarketReportPackageError(500, error_output)
    rebuilt_lines = (getattr(completed, "stdout", "") or "").strip().splitlines()
    if not rebuilt_lines:
        raise MarketReportPackageError(500, "US SEC package rebuild did not return a package path")
    detail = read_package_detail(safe_package_path(str(Path(rebuilt_lines[-1]))))
    return market_report_commands.us_sec_rebuild_package_result_payload(
        completed=completed,
        ticker=plan.ticker,
        package=detail,
    )
