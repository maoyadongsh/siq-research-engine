#!/usr/bin/env python3
"""Run SIQ market document_full PostgreSQL gates.

`contract` is the PR-safe mode: it does not connect to PostgreSQL and does not
perform Wiki/PostgreSQL parity. `offline-postgres` runs the strict production
gate and requires a prepared PostgreSQL environment.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
BACKTEST_DIR = REPO_ROOT / "db" / "imports" / "backtests"
if str(BACKTEST_DIR) not in sys.path:
    sys.path.insert(0, str(BACKTEST_DIR))

from market_document_full_postgres_backtest import (  # noqa: E402
    DEFAULT_CASES_PATH,
    DEFAULT_PRODUCTION_SAMPLE_MANIFEST_PATH,
    run_cases,
    write_report,
)


DEFAULT_OUTPUT_DIR = REPO_ROOT / "artifacts" / "eval-runs" / "ci"
MODE_OUTPUT_STEMS = {
    "contract": "market_document_full_postgres_contract_gate",
    "offline-postgres": "market_document_full_postgres_offline_postgres_gate",
}


def _repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _report_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    output_dir = _repo_path(args.output_dir)
    stem = MODE_OUTPUT_STEMS[args.mode]
    json_output = _repo_path(args.json_output) if args.json_output else output_dir / f"{stem}.json"
    markdown_output = _repo_path(args.markdown_output) if args.markdown_output else output_dir / f"{stem}.md"
    return json_output, markdown_output


def _contract_summary_is_clean(summary: dict[str, Any]) -> bool:
    return not any(
        (
            summary.get("db_results"),
            summary.get("production_sample_db_results"),
            summary.get("production_sample_db_coexistence_results"),
            summary.get("production_agent_results"),
            summary.get("wiki_postgres_parity_results"),
            summary.get("production_sample_wiki_postgres_parity_results"),
            (summary.get("summary") or {}).get("postgres_import_executed"),
        )
    )


def _result_identity(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item)
    parts = [
        item.get("market"),
        item.get("case_id") or item.get("id"),
        item.get("sample_id"),
        item.get("metric") or item.get("canonical_name") or item.get("metric_name"),
        item.get("status"),
    ]
    return " ".join(str(part) for part in parts if part not in (None, "", [], {}))


def _result_messages(item: Any) -> list[str]:
    if not isinstance(item, dict):
        return [str(item)]
    messages: list[str] = []
    for key in ("errors", "warnings", "missing_counts", "gate_failures"):
        value = item.get(key)
        if isinstance(value, list):
            messages.extend(str(entry) for entry in value if entry not in (None, ""))
        elif value not in (None, "", [], {}):
            messages.append(str(value))
    for key in ("error", "warning", "message", "reason"):
        value = item.get(key)
        if value not in (None, "", [], {}):
            messages.append(str(value))
    return messages


def _failure_summary_lines(summary: dict[str, Any], *, limit: int = 12) -> list[str]:
    lines: list[str] = []
    failed_requirements = [
        key
        for key, value in (summary.get("acceptance_requirements") or {}).items()
        if value is False
    ]
    if failed_requirements:
        lines.append("Failed acceptance requirements: " + ", ".join(failed_requirements))

    result_keys = (
        "results",
        "agent_results",
        "db_results",
        "production_sample_db_results",
        "production_sample_db_coexistence_results",
        "production_agent_results",
        "wiki_postgres_parity_results",
        "production_sample_wiki_postgres_parity_results",
    )
    for key in result_keys:
        for item in summary.get(key) or []:
            if len(lines) >= limit:
                lines.append(f"... truncated after {limit} failure summary lines")
                return lines
            messages = _result_messages(item)
            status = str(item.get("status") or "").lower() if isinstance(item, dict) else ""
            passed = item.get("passed") if isinstance(item, dict) else None
            if not messages and passed is not False and status not in {"fail", "failed", "warning", "missing", "unknown"}:
                continue
            identity = _result_identity(item) or key
            suffix = "; ".join(messages[:3]) if messages else "status=" + (status or str(passed))
            lines.append(f"{key}: {identity}: {suffix}")
    return lines


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run market document_full PostgreSQL release gates.")
    parser.add_argument("--mode", choices=("contract", "offline-postgres"), default="contract")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH, help="Path to backtest cases.json.")
    parser.add_argument(
        "--production-sample-manifest",
        type=Path,
        default=DEFAULT_PRODUCTION_SAMPLE_MANIFEST_PATH,
        help="Path to the real-sample manifest used by the underlying backtest.",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--json-output", type=Path, default=None, help="Override the JSON artifact path.")
    parser.add_argument("--markdown-output", type=Path, default=None, help="Override the Markdown artifact path.")
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--json", action="store_true", help="Print the full JSON summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.mode == "contract" and args.database_url:
        raise SystemExit("--database-url is only valid with --mode offline-postgres")

    if args.mode == "contract":
        summary = run_cases(
            _repo_path(args.cases),
            verify_db=False,
            database_url=None,
            import_before_db_check=False,
            idempotency=False,
            production_sample_manifest_path=_repo_path(args.production_sample_manifest),
            require_production_sample_files=False,
            production_sample_db=False,
            production_agent_query=False,
        )
        gate_passed = bool(summary.get("passed")) and _contract_summary_is_clean(summary)
        summary["gate_mode"] = "contract"
        summary["gate_passed"] = gate_passed
    else:
        summary = run_cases(
            _repo_path(args.cases),
            verify_db=True,
            database_url=args.database_url,
            import_before_db_check=True,
            idempotency=True,
            production_sample_manifest_path=_repo_path(args.production_sample_manifest),
            require_production_sample_files=True,
            production_sample_db=True,
            production_agent_query=True,
        )
        gate_passed = bool(summary.get("acceptance_passed"))
        summary["gate_mode"] = "offline-postgres"
        summary["gate_passed"] = gate_passed

    output_path, markdown_path = _report_paths(args)
    write_report(summary, output_path, markdown_path)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"{'PASS' if gate_passed else 'FAIL'} market document_full gate mode={args.mode}")
        print(f"JSON: {output_path}")
        print(f"Markdown: {markdown_path}")
        print(f"Fixture contract passed: {summary.get('passed')}")
        print(f"Acceptance passed: {summary.get('acceptance_passed')}")
        if args.mode == "contract" and not _contract_summary_is_clean(summary):
            print("Contract mode unexpectedly produced DB/parity/production query results.")
        if not gate_passed:
            for line in _failure_summary_lines(summary):
                print(line)
    return 0 if gate_passed else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
