"""Financial artifact path and cache helpers for PDF parser results."""

from __future__ import annotations

import json
import os
from typing import Any, Callable

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
    return (
        isinstance(financial_data, dict)
        and isinstance(financial_checks, dict)
        and financial_data.get("schema_version") == FINANCIAL_DATA_SCHEMA_VERSION
        and financial_checks.get("schema_version") == FINANCIAL_CHECKS_SCHEMA_VERSION
        and financial_data.get("rule_version") == FINANCIAL_RULE_VERSION
        and financial_checks.get("rule_version") == FINANCIAL_RULE_VERSION
    )


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
    financial_data = build_data(
        markdown,
        task_id=task.get("task_id"),
        filename=file_name or task.get("filename"),
        llm_cache_dir=os.path.join(financial_llm_cache_folder, task.get("task_id") or "unknown"),
    )
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
    if financial_artifacts_are_current(financial_data, financial_checks):
        return financial_data, financial_checks
    return write_financial_artifacts(
        task,
        markdown,
        result_dir=result_dir,
        write_json=write_json,
        financial_llm_cache_folder=financial_llm_cache_folder,
        file_name=file_name or task.get("filename"),
    )
