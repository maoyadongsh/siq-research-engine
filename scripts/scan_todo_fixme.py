#!/usr/bin/env python3
"""Advisory TODO/FIXME scanner for source governance reports."""

from __future__ import annotations

import argparse
import fnmatch
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".audit-venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "artifacts",
    "build",
    "coverage",
    "data",
    "dist",
    "node_modules",
    "playwright-report",
    "runtimes",
    "test-results",
    "var",
    ".venv",
    "venv",
}
DEFAULT_EXCLUDE_GLOBS = {
    "*.map",
    "*.pyc",
    "*.pyo",
    "*debt-marker-governance-report.md",
    "*todo-fixme-governance-report.md",
    "scan_todo_fixme.py",
}
TODO_RE = re.compile(r"\b(TODO|FIXME)\b")

BUCKET_LABELS = {
    "safety": "安全",
    "runtime": "运行时",
    "architecture": "架构",
    "docs_quality": "文档/质量规则",
}

BUCKET_KEYWORDS = {
    "safety": (
        "auth",
        "csrf",
        "encrypt",
        "permission",
        "privacy",
        "rbac",
        "sanitize",
        "secret",
        "security",
        "token",
        "xss",
    ),
    "runtime": (
        "async",
        "cache",
        "connection",
        "database",
        "error",
        "exception",
        "failure",
        "health",
        "latency",
        "memory",
        "queue",
        "retry",
        "shutdown",
        "startup",
        "timeout",
        "worker",
    ),
    "architecture": (
        "adapter",
        "boundary",
        "compat",
        "contract",
        "dependency",
        "facade",
        "legacy",
        "migration",
        "owner",
        "refactor",
        "repository",
        "schema",
        "service",
        "split",
        "wrapper",
    ),
    "docs_quality": (
        "coverage",
        "doc",
        "docs",
        "eslint",
        "format",
        "lint",
        "quality",
        "readme",
        "style",
        "test",
        "type",
    ),
}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    marker: str
    text: str
    bucket: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan TODO/FIXME comments and classify them into advisory buckets."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to scan. Defaults to the current directory.",
    )
    parser.add_argument(
        "--markdown",
        metavar="PATH",
        help="Write a Markdown report to PATH.",
    )
    parser.add_argument(
        "--max-examples",
        type=int,
        default=25,
        help="Maximum findings to print per bucket in console output. Markdown includes all findings.",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="Additional directory basename to exclude. Can be repeated.",
    )
    parser.add_argument(
        "--exclude-glob",
        action="append",
        default=[],
        help="Additional file glob to exclude. Can be repeated.",
    )
    return parser.parse_args()


def should_skip_dir(path: Path, exclude_dirs: set[str]) -> bool:
    return path.name in exclude_dirs


def should_skip_file(path: Path, exclude_globs: set[str]) -> bool:
    return any(fnmatch.fnmatch(path.name, pattern) for pattern in exclude_globs)


def iter_files(root: Path, exclude_dirs: set[str], exclude_globs: set[str]) -> Iterable[Path]:
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        if child.is_dir():
            if should_skip_dir(child, exclude_dirs):
                continue
            yield from iter_files(child, exclude_dirs, exclude_globs)
        elif child.is_file() and not should_skip_file(child, exclude_globs):
            yield child


def classify(path: str, text: str) -> str:
    haystack = f"{path} {text}".lower()
    if "quality_gate" in haystack or "placeholder" in haystack or "待补充" in text:
        return "docs_quality"

    scores = {
        bucket: sum(1 for keyword in keywords if keyword in haystack)
        for bucket, keywords in BUCKET_KEYWORDS.items()
    }
    best_bucket, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score > 0:
        return best_bucket
    if path.startswith("docs/") or path.endswith(".md"):
        return "docs_quality"
    if "/tests/" in path or path.startswith("tests/"):
        return "docs_quality"
    return "architecture"


def scan(root: Path, exclude_dirs: set[str], exclude_globs: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    for path in iter_files(root, exclude_dirs, exclude_globs):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        rel_path = path.relative_to(root).as_posix()
        for index, line in enumerate(lines, start=1):
            match = TODO_RE.search(line)
            if not match:
                continue
            text = line.strip()
            findings.append(
                Finding(
                    path=rel_path,
                    line=index,
                    marker=match.group(1).upper(),
                    text=text,
                    bucket=classify(rel_path, text),
                )
            )
    return findings


def bucket_counts(findings: Iterable[Finding]) -> Counter[str]:
    return Counter(finding.bucket for finding in findings)


def print_summary(findings: list[Finding], max_examples: int) -> None:
    counts = bucket_counts(findings)
    print("TODO/FIXME advisory scan")
    print(f"total: {len(findings)}")
    for bucket in BUCKET_LABELS:
        print(f"{BUCKET_LABELS[bucket]}: {counts.get(bucket, 0)}")

    for bucket in BUCKET_LABELS:
        bucket_findings = [finding for finding in findings if finding.bucket == bucket]
        if not bucket_findings:
            continue
        print()
        print(f"[{BUCKET_LABELS[bucket]}] examples")
        for finding in bucket_findings[:max_examples]:
            print(f"- {finding.path}:{finding.line} {finding.marker}: {finding.text}")
        if len(bucket_findings) > max_examples:
            print(f"- ... {len(bucket_findings) - max_examples} more")


def markdown_report(
    findings: list[Finding],
    root: Path,
    exclude_dirs: set[str],
    exclude_globs: set[str],
) -> str:
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    counts = bucket_counts(findings)
    lines = [
        "# TODO/FIXME 治理报告",
        "",
        f"生成时间：{generated_at}",
        f"扫描根目录：`{root}`",
        "",
        "## 摘要",
        "",
        "| 分桶 | 数量 |",
        "| --- | ---: |",
    ]
    for bucket in BUCKET_LABELS:
        lines.append(f"| {BUCKET_LABELS[bucket]} | {counts.get(bucket, 0)} |")
    lines.extend(
        [
            f"| 合计 | {len(findings)} |",
            "",
            "默认排除目录/文件："
            + ", ".join(f"`{item}`" for item in sorted(exclude_dirs | exclude_globs)),
            "",
            "本报告是非阻断 advisory 输出，只用于后续治理分诊，不接入硬 CI。",
            "",
        ]
    )

    for bucket in BUCKET_LABELS:
        bucket_findings = [finding for finding in findings if finding.bucket == bucket]
        lines.extend([f"## {BUCKET_LABELS[bucket]}", ""])
        if not bucket_findings:
            lines.extend(["暂无发现。", ""])
            continue
        lines.extend(["| 文件 | 行 | 标记 | 内容 |", "| --- | ---: | --- | --- |"])
        for finding in bucket_findings:
            text = finding.text.replace("|", "\\|")
            lines.append(
                f"| `{finding.path}` | {finding.line} | {finding.marker} | {text} |"
            )
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    root = Path(args.root).resolve()
    exclude_dirs = DEFAULT_EXCLUDE_DIRS | set(args.exclude_dir)
    exclude_globs = DEFAULT_EXCLUDE_GLOBS | set(args.exclude_glob)
    findings = scan(root, exclude_dirs, exclude_globs)

    print_summary(findings, args.max_examples)

    if args.markdown:
        report_path = Path(args.markdown)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            markdown_report(findings, root, exclude_dirs, exclude_globs),
            encoding="utf-8",
        )
        print()
        print(f"markdown: {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
