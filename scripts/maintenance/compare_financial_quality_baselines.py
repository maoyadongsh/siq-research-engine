#!/usr/bin/env python3
"""Compare SIQ financial-quality and retrieval-performance gate artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
QUALITY_RATE_FIELDS = (
    "key_fact_accuracy",
    "period_unit_currency_accuracy",
    "evidence_coverage_rate",
    "source_policy_pass_rate",
    "calculator_input_ready_rate",
    "calculator_run_accuracy",
)
DOMAIN_COVERAGE_FIELDS = (
    "cases",
    "passed_cases",
    "passed_count",
    "chunks",
    "input_count",
    "vector_count",
    "top_k",
    "hit_rate",
    "mrr",
    "texts_per_second",
    "chars_per_second",
)
DEFAULT_P95_BUDGETS = {
    "market_ingestion_contract": 5.0,
    "market_document_full_contract": 10.0,
    "market_evidence_chunk_builder": 10.0,
    "postgres_agent_view_query_latency": 10.0,
    "agent_memory_embedding_throughput": 10.0,
    "agent_memory_milvus_retrieval_latency": 10.0,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_output(*args: str) -> str:
    try:
        return subprocess.run(
            ("git", *args),
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _relative_name(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.name


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def compare_financial_report(
    baseline: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    mode = str(baseline.get("mode") or "unknown")
    failures: list[str] = []
    before = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else {}
    after = current.get("summary") if isinstance(current.get("summary"), dict) else {}
    if current.get("mode") != baseline.get("mode"):
        failures.append(f"financial mode mismatch: {baseline.get('mode')!r} != {current.get('mode')!r}")
    if not baseline.get("passed"):
        failures.append(f"financial baseline mode={mode} is not passing")
    if not current.get("passed"):
        failures.append(f"financial current mode={mode} is not passing")
    for field in QUALITY_RATE_FIELDS:
        baseline_value = _number(before.get(field))
        current_value = _number(after.get(field))
        if baseline_value is None or current_value is None:
            failures.append(f"financial mode={mode} missing numeric summary.{field}")
        elif current_value < baseline_value:
            failures.append(f"financial mode={mode} {field} regressed: {baseline_value:.6f} -> {current_value:.6f}")
    for field in ("cases", "passed_cases", "guardrail_block_count"):
        baseline_value = _number(before.get(field))
        current_value = _number(after.get(field))
        if baseline_value is not None and (current_value is None or current_value < baseline_value):
            failures.append(
                f"financial mode={mode} {field} decreased: {baseline_value:g} -> "
                f"{current_value if current_value is not None else 'missing'}"
            )
    return {
        "mode": mode,
        "passed": not failures,
        "before": {
            field: before.get(field)
            for field in (*QUALITY_RATE_FIELDS, "cases", "passed_cases", "guardrail_block_count")
        },
        "after": {
            field: after.get(field)
            for field in (*QUALITY_RATE_FIELDS, "cases", "passed_cases", "guardrail_block_count")
        },
        "failures": failures,
    }


def _benchmarks(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = report.get("benchmarks") if isinstance(report.get("benchmarks"), list) else []
    return {str(row.get("name")): row for row in rows if isinstance(row, dict) and row.get("name")}


def compare_performance_report(
    baseline: dict[str, Any],
    current: dict[str, Any],
    *,
    p95_budgets: dict[str, float] | None = None,
) -> dict[str, Any]:
    budgets = {**DEFAULT_P95_BUDGETS, **(p95_budgets or {})}
    failures: list[str] = []
    comparisons: list[dict[str, Any]] = []
    baseline_rows = _benchmarks(baseline)
    current_rows = _benchmarks(current)
    if baseline.get("mode") != current.get("mode"):
        failures.append(f"performance mode mismatch: {baseline.get('mode')!r} != {current.get('mode')!r}")
    baseline_settings = baseline.get("settings") if isinstance(baseline.get("settings"), dict) else {}
    current_settings = current.get("settings") if isinstance(current.get("settings"), dict) else {}
    for field in ("repeat", "max_benchmark_seconds"):
        if field in baseline_settings and baseline_settings.get(field) != current_settings.get(field):
            failures.append(
                f"performance setting mismatch for {field}: "
                f"{baseline_settings.get(field)!r} != {current_settings.get(field)!r}"
            )
    if not baseline.get("passed"):
        failures.append("performance baseline is not passing")
    if not current.get("passed"):
        failures.append("performance current report is not passing")
    for name, before in baseline_rows.items():
        after = current_rows.get(name)
        row_failures: list[str] = []
        if after is None:
            row_failures.append("benchmark missing from current report")
            comparisons.append({"name": name, "passed": False, "failures": row_failures})
            failures.extend(f"performance {name}: {item}" for item in row_failures)
            continue
        if not after.get("passed"):
            row_failures.append("current benchmark is not passing")
        before_domain = before.get("domain") if isinstance(before.get("domain"), dict) else {}
        after_domain = after.get("domain") if isinstance(after.get("domain"), dict) else {}
        for field in DOMAIN_COVERAGE_FIELDS:
            before_value = _number(before_domain.get(field))
            after_value = _number(after_domain.get(field))
            if before_value is not None and (after_value is None or after_value < before_value):
                row_failures.append(
                    f"domain.{field} decreased: {before_value:g} -> "
                    f"{after_value if after_value is not None else 'missing'}"
                )
        before_p95 = _number((before.get("elapsed_ms") or {}).get("p95"))
        after_p95 = _number((after.get("elapsed_ms") or {}).get("p95"))
        budget_percent = budgets.get(name, 10.0)
        allowed_p95 = before_p95 * (1 + budget_percent / 100) if before_p95 is not None else None
        if before_p95 is None or after_p95 is None:
            row_failures.append("elapsed_ms.p95 is missing")
        elif after_p95 > (allowed_p95 or 0):
            row_failures.append(
                f"p95 regression exceeds {budget_percent:g}% budget: {before_p95:.3f}ms -> {after_p95:.3f}ms"
            )
        comparison = {
            "name": name,
            "passed": not row_failures,
            "p95_budget_percent": budget_percent,
            "before_p95_ms": before_p95,
            "after_p95_ms": after_p95,
            "change_percent": round((after_p95 / before_p95 - 1) * 100, 3)
            if before_p95 and after_p95 is not None
            else None,
            "failures": row_failures,
        }
        comparisons.append(comparison)
        failures.extend(f"performance {name}: {item}" for item in row_failures)
    return {"passed": not failures, "benchmarks": comparisons, "failures": failures}


def _parse_budget(raw: str) -> tuple[str, float]:
    name, separator, value = raw.partition("=")
    if not separator or not name.strip():
        raise argparse.ArgumentTypeError("budget must be BENCHMARK=PERCENT")
    try:
        percent = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("budget percent must be numeric") from exc
    if percent < 0:
        raise argparse.ArgumentTypeError("budget percent must be non-negative")
    return name.strip(), percent


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# T10 Financial Quality And Performance Comparison",
        "",
        f"- Generated at: `{report['generated_at']}`",
        f"- Base commit: `{report['base_commit']}`",
        f"- Worktree dirty: `{report['worktree_dirty']}`",
        f"- Environment: `{report['environment_profile']}`",
        f"- Result: **{report['result'].upper()}**",
        "",
        "## Financial Quality",
        "",
        "| Mode | Status | Cases | Evidence | Source policy | Calculator | Guard blocks |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in report["comparisons"]["financial"]:
        after = item["after"]
        lines.append(
            f"| {item['mode']} | {'PASS' if item['passed'] else 'FAIL'} | {after.get('cases')} | "
            f"{after.get('evidence_coverage_rate')} | {after.get('source_policy_pass_rate')} | "
            f"{after.get('calculator_run_accuracy')} | {after.get('guardrail_block_count')} |"
        )
    lines.extend(
        [
            "",
            "## Performance",
            "",
            "| Benchmark | Status | Before P95 ms | After P95 ms | Change | Budget |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for item in report["comparisons"]["performance"]["benchmarks"]:
        change = item.get("change_percent")
        lines.append(
            f"| {item['name']} | {'PASS' if item['passed'] else 'FAIL'} | {item.get('before_p95_ms')} | "
            f"{item.get('after_p95_ms')} | {change if change is not None else 'n/a'}% | "
            f"{item.get('p95_budget_percent', 'n/a')}% |"
        )
    failures = report.get("failures") or []
    lines.extend(["", "## Failures", ""])
    lines.extend(f"- {failure}" for failure in failures) if failures else lines.append("- None")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline-financial", type=Path, action="append", default=[])
    parser.add_argument("--current-financial", type=Path, action="append", default=[])
    parser.add_argument("--baseline-performance", type=Path, required=True)
    parser.add_argument("--current-performance", type=Path, required=True)
    parser.add_argument("--p95-budget", action="append", default=[], type=_parse_budget)
    parser.add_argument("--base-commit", default="")
    parser.add_argument("--environment-profile", default="pr-deterministic")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    args = build_parser().parse_args(argv)
    if len(args.baseline_financial) != len(args.current_financial):
        raise SystemExit("baseline/current financial report counts must match")
    input_paths = [
        *args.baseline_financial,
        *args.current_financial,
        args.baseline_performance,
        args.current_performance,
    ]
    financial = [
        compare_financial_report(_load(before), _load(after))
        for before, after in zip(args.baseline_financial, args.current_financial, strict=True)
    ]
    performance = compare_performance_report(
        _load(args.baseline_performance),
        _load(args.current_performance),
        p95_budgets=dict(args.p95_budget),
    )
    failures = [failure for item in financial for failure in item["failures"]] + performance["failures"]
    report = {
        "schema_version": "siq_financial_quality_comparison_v1",
        "generated_at": _now_iso(),
        "base_commit": args.base_commit or _git_output("rev-parse", "HEAD"),
        "worktree_dirty": bool(_git_output("status", "--porcelain")),
        "task_id": "T10",
        "environment_profile": args.environment_profile,
        "command": "compare_financial_quality_baselines.py (paths and credentials omitted)",
        "result": "pass" if not failures else "fail",
        "duration_seconds": round(time.perf_counter() - started, 3),
        "failures": failures,
        "artifact_checksums": {_relative_name(path): _sha256(path) for path in input_paths},
        "comparisons": {"financial": financial, "performance": performance},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(render_markdown(report), encoding="utf-8")
    print(f"{'PASS' if not failures else 'FAIL'} T10 financial-quality/performance comparison")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
