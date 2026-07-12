from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from services import market_report_commands


class MarketReportEvalError(Exception):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def run_market_ingestion_eval(
    *,
    payload: Mapping[str, Any],
    eval_script: Path,
    repo_root: Path,
    default_output: Path,
    default_markdown: Path,
    executable: str,
    run_command: Callable[..., Any],
    command_for_display: Callable[[list[str]], str],
    read_json_file: Callable[[Path, Any], Any],
    rel_or_abs: Callable[[Path], str],
    timeout: int = 900,
) -> dict[str, Any]:
    try:
        plan = market_report_commands.build_market_ingestion_eval_plan(
            payload=payload,
            eval_script=eval_script,
            repo_root=repo_root,
            default_output=default_output,
            default_markdown=default_markdown,
        )
    except market_report_commands.MarketPackagePlanError as exc:
        raise MarketReportEvalError(exc.status_code, exc.detail) from exc

    args, _output, _markdown = market_report_commands.market_ingestion_eval_args(
        executable=executable,
        script=plan.script,
        payload={},
        repo_root=repo_root,
        default_output=plan.output_path,
        default_markdown=plan.markdown_path,
    )
    completed = run_command(args, cwd=repo_root, timeout=timeout)
    return market_report_commands.market_ingestion_eval_result_payload(
        completed=completed,
        report=read_json_file(plan.output_path, {}),
        markdown_path=rel_or_abs(plan.markdown_path),
        command=command_for_display(args),
    )
