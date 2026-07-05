"""Financial artifact path and cache helpers for PDF parser results."""

from __future__ import annotations

import json
import os
import inspect
import re
from typing import Any, Callable

from hk_financial_artifacts import HK_FINANCIAL_PROFILE_VERSION, build_hk_financial_artifacts
from jp_financial_artifacts import JP_FINANCIAL_PROFILE_VERSION, build_jp_financial_artifacts
from kr_financial_artifacts import KR_FINANCIAL_PROFILE_VERSION, build_kr_financial_artifacts

from financial_extractor import (
    FINANCIAL_CHECKS_SCHEMA_VERSION,
    FINANCIAL_DATA_SCHEMA_VERSION,
    FINANCIAL_RULE_VERSION,
    build_financial_checks,
    build_financial_data,
)


def financial_data_path(task: dict[str, Any], result_dir: Callable[[dict[str, Any]], str]) -> str:
    return os.path.join(result_dir(task), "financial_data.json")


def financial_checks_path(task: dict[str, Any], result_dir: Callable[[dict[str, Any]], str]) -> str:
    return os.path.join(result_dir(task), "financial_checks.json")


def read_financial_artifacts(
    task: dict[str, Any],
    result_dir: Callable[[dict[str, Any]], str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    data_path = financial_data_path(task, result_dir)
    checks_path = financial_checks_path(task, result_dir)
    if not os.path.exists(data_path) or not os.path.exists(checks_path):
        return None, None
    with open(data_path, "r", encoding="utf-8") as infile:
        data = json.load(infile)
    with open(checks_path, "r", encoding="utf-8") as infile:
        checks = json.load(infile)
    return data, checks


def financial_artifacts_are_current(
    financial_data: Any,
    financial_checks: Any,
) -> bool:
    if (
        isinstance(financial_data, dict)
        and isinstance(financial_checks, dict)
        and str(financial_data.get("market") or "").upper() == "US"
        and str(financial_checks.get("market") or "").upper() == "US"
        and financial_data.get("schema_version") == 1
        and financial_checks.get("schema_version") == 1
        and financial_data.get("rule_version") == "us_sec_rules_v1"
        and financial_checks.get("rule_version") == "us_sec_rules_v1"
    ):
        return True

    def profile_is_current() -> bool:
        market = str((financial_data or {}).get("market") or "").upper()
        if market == "HK":
            return (
                financial_data.get("profile_rule_version") == HK_FINANCIAL_PROFILE_VERSION
                and financial_checks.get("profile_rule_version") == HK_FINANCIAL_PROFILE_VERSION
            )
        if market == "JP":
            return (
                financial_data.get("profile_rule_version") == JP_FINANCIAL_PROFILE_VERSION
                and financial_checks.get("profile_rule_version") == JP_FINANCIAL_PROFILE_VERSION
            )
        if market == "KR":
            return (
                financial_data.get("profile_rule_version") == KR_FINANCIAL_PROFILE_VERSION
                and financial_checks.get("profile_rule_version") == KR_FINANCIAL_PROFILE_VERSION
            )
        if market != "EU":
            return True
        try:
            import eu_market_profile as eu
        except Exception:
            return True
        expected = eu.EU_PROFILE_RULE_VERSION
        return (
            financial_data.get("profile_rule_version") == expected
            and financial_checks.get("profile_rule_version") == expected
        )

    return (
        isinstance(financial_data, dict)
        and isinstance(financial_checks, dict)
        and financial_data.get("schema_version") == FINANCIAL_DATA_SCHEMA_VERSION
        and financial_checks.get("schema_version") == FINANCIAL_CHECKS_SCHEMA_VERSION
        and financial_data.get("rule_version") == FINANCIAL_RULE_VERSION
        and financial_checks.get("rule_version") == FINANCIAL_RULE_VERSION
        and profile_is_current()
    )


def financial_artifacts_match_market(
    market: str | None,
    financial_data: Any,
    financial_checks: Any,
) -> bool:
    market = str(market or "").upper()
    if market not in {"HK", "JP", "KR", "EU", "US"}:
        return True
    return (
        isinstance(financial_data, dict)
        and isinstance(financial_checks, dict)
        and str(financial_data.get("market") or "").upper() == market
        and str(financial_checks.get("market") or "").upper() == market
    )


def _call_build_financial_data(
    build_data: Callable[..., dict[str, Any]],
    markdown: str,
    **kwargs,
) -> dict[str, Any]:
    try:
        signature = inspect.signature(build_data)
        accepts_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values())
        if not accepts_kwargs:
            kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    except (TypeError, ValueError):
        pass
    return build_data(markdown, **kwargs)


def detect_market(task: dict[str, Any], filename: str | None = None) -> str:
    submit_config = task.get("submit_config") if isinstance(task.get("submit_config"), dict) else {}
    explicit = submit_config.get("market") or task.get("market")
    if explicit:
        return str(explicit).upper()
    name = str(filename or task.get("filename") or "")
    lowered = name.lower()
    if "_hk_" in lowered or "hkex" in lowered or "sehk" in lowered:
        return "HK"
    if "_jp_" in lowered or "edinet" in lowered:
        return "JP"
    if "_kr_" in lowered or "dart_public" in lowered:
        return "KR"
    if re.search(r"(?:^|[_\-])eu(?:[_\-]|$)", lowered) or "issuer_annual_report" in lowered:
        return "EU"
    if "_us_" in lowered or "sec" in lowered or "10-k" in lowered or "10k" in lowered:
        return "US"
    if "_cn_" in lowered or "sse" in lowered or "szse" in lowered or "bse" in lowered:
        return "CN"
    return "CN"


def write_financial_artifacts(
    task: dict[str, Any],
    markdown: str,
    *,
    result_dir: Callable[[dict[str, Any]], str],
    write_json: Callable[[str, Any], None],
    financial_llm_cache_folder: str,
    file_name: str | None = None,
    build_data: Callable[..., dict[str, Any]] = build_financial_data,
    build_checks: Callable[[dict[str, Any]], dict[str, Any]] = build_financial_checks,
) -> tuple[dict[str, Any], dict[str, Any]]:
    directory = result_dir(task)
    os.makedirs(directory, exist_ok=True)
    resolved_filename = file_name or task.get("filename")
    market = detect_market(task, resolved_filename)
    if market == "HK":
        financial_data, financial_checks = build_hk_financial_artifacts(
            task,
            markdown,
            result_dir_path=directory,
            filename=resolved_filename,
        )
    elif market == "JP":
        financial_data, financial_checks = build_jp_financial_artifacts(
            task,
            markdown,
            result_dir_path=directory,
            filename=resolved_filename,
        )
    elif market == "KR":
        financial_data, financial_checks = build_kr_financial_artifacts(
            task,
            markdown,
            result_dir_path=directory,
            filename=resolved_filename,
        )
    else:
        financial_data = _call_build_financial_data(
            build_data,
            markdown,
            task_id=task.get("task_id"),
            filename=resolved_filename,
            market=market if market in {"JP", "KR", "EU", "US"} else None,
            llm_cache_dir=os.path.join(financial_llm_cache_folder, task.get("task_id") or "unknown"),
        )
        if market in {"JP", "KR", "EU", "US"} and isinstance(financial_data, dict):
            financial_data["market"] = market
        financial_checks = build_checks(financial_data)
    write_json(financial_data_path(task, result_dir), financial_data)
    write_json(financial_checks_path(task, result_dir), financial_checks)
    return financial_data, financial_checks


def ensure_financial_artifacts(
    task: dict[str, Any],
    markdown: str,
    *,
    result_dir: Callable[[dict[str, Any]], str],
    write_json: Callable[[str, Any], None],
    financial_llm_cache_folder: str,
    file_name: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    financial_data, financial_checks = read_financial_artifacts(task, result_dir)
    resolved_filename = file_name or task.get("filename")
    market = detect_market(task, resolved_filename)
    if financial_artifacts_are_current(financial_data, financial_checks) and financial_artifacts_match_market(
        market,
        financial_data,
        financial_checks,
    ):
        return financial_data, financial_checks
    return write_financial_artifacts(
        task,
        markdown,
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=financial_llm_cache_folder,
        file_name=resolved_filename,
    )
