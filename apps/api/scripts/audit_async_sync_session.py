#!/usr/bin/env python3
"""Audit sync SQLModel Session usage inside async API functions.

This tool is intentionally advisory by default. It reports current findings so
architecture work can be planned, while tests can apply an allowlist to prevent
new sync Session usage from spreading before the DB owner is migrated.
"""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, TypedDict


BACKEND_ROOT = Path(__file__).resolve().parents[1]

PRIORITY_ORDER = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
PRIORITY_LEGEND = {
    "P0": "inline sync Session acquisition inside async code",
    "P1": "large module-level route surface",
    "P2": "medium module-level route surface",
    "P3": "small isolated route surface",
}
ADVISORY_NOTES = (
    "Advisory report only; this script exits 0 by default.",
    "Buckets are for planning async DB ownership slices, not a migration gate.",
)


class AdvisoryBucket(TypedDict):
    priority: str
    path: str
    total: int
    depends_get_session: int
    next_get_session: int
    recommendation: str


@dataclass(frozen=True, order=True)
class SyncSessionFinding:
    path: str
    qualname: str
    kind: str
    detail: str

    @property
    def key(self) -> str:
        return f"{self.path}::{self.qualname}::{self.kind} {self.detail}"


def default_scan_paths(backend_root: Path = BACKEND_ROOT) -> list[Path]:
    return [
        *sorted((backend_root / "routers").glob("*.py")),
        backend_root / "services" / "auth_dependencies.py",
    ]


def _argument_defaults(node: ast.AsyncFunctionDef) -> dict[int, ast.expr | None]:
    positional = [*node.args.posonlyargs, *node.args.args]
    positional_defaults = [None] * (len(positional) - len(node.args.defaults)) + list(node.args.defaults)
    defaults = {id(arg): default for arg, default in zip(positional, positional_defaults)}
    defaults.update(
        {id(arg): default for arg, default in zip(node.args.kwonlyargs, node.args.kw_defaults)}
    )
    return defaults


class _NextGetSessionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.found = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        if isinstance(node.func, ast.Name) and node.func.id == "next":
            if node.args and ast.unparse(node.args[0]) == "get_session()":
                self.found = True
        self.generic_visit(node)


def _iter_async_functions(nodes: Iterable[ast.stmt], parents: tuple[str, ...] = ()):
    for node in nodes:
        if isinstance(node, ast.AsyncFunctionDef):
            qualname = ".".join((*parents, node.name))
            yield qualname, node
            yield from _iter_async_functions(node.body, (*parents, node.name))
        elif isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            yield from _iter_async_functions(node.body, (*parents, node.name))


def iter_sync_session_findings(backend_root: Path = BACKEND_ROOT) -> list[SyncSessionFinding]:
    findings: list[SyncSessionFinding] = []
    for path in default_scan_paths(backend_root):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        relative_path = path.relative_to(backend_root).as_posix()
        for qualname, node in _iter_async_functions(tree.body):
            defaults = _argument_defaults(node)
            for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
                default = defaults.get(id(arg))
                if default is None:
                    continue
                default_text = ast.unparse(default)
                if "Depends(get_session)" not in default_text:
                    continue
                annotation = ast.unparse(arg.annotation) if arg.annotation else "<unannotated>"
                findings.append(
                    SyncSessionFinding(
                        path=relative_path,
                        qualname=qualname,
                        kind=f"param {arg.arg}:",
                        detail=f"{annotation} = {default_text}",
                    )
                )

            visitor = _NextGetSessionVisitor()
            for statement in node.body:
                visitor.visit(statement)
            if visitor.found:
                findings.append(
                    SyncSessionFinding(
                        path=relative_path,
                        qualname=qualname,
                        kind="body",
                        detail="next(get_session())",
                    )
                )
    return sorted(findings)


def sync_session_usage(backend_root: Path = BACKEND_ROOT) -> set[str]:
    return {finding.key for finding in iter_sync_session_findings(backend_root)}


def finding_summary(findings: Iterable[SyncSessionFinding]) -> dict[str, object]:
    findings = list(findings)
    by_path = Counter(finding.path for finding in findings)
    by_kind = Counter(_finding_kind_key(finding) for finding in findings)
    return {
        "total": len(findings),
        "by_kind": dict(sorted(by_kind.items())),
        "by_path": dict(sorted(by_path.items())),
    }


def _finding_kind_key(finding: SyncSessionFinding) -> str:
    return "next_get_session" if finding.kind == "body" else "depends_get_session"


def _bucket_priority(total: int, next_get_session: int) -> tuple[str, str]:
    if next_get_session:
        return (
            "P0",
            "Audit inline next(get_session()) call sites before dependency-only modules.",
        )
    if total >= 10:
        return (
            "P1",
            "Plan as an owner-sized module slice; migrate dependency injection consistently.",
        )
    if total >= 3:
        return (
            "P2",
            "Batch with related service/data-access cleanup when the owner slice is active.",
        )
    return (
        "P3",
        "Keep advisory; defer until the parent owner migration reaches this module.",
    )


def advisory_buckets(findings: Iterable[SyncSessionFinding]) -> list[AdvisoryBucket]:
    grouped: dict[str, list[SyncSessionFinding]] = {}
    for finding in findings:
        grouped.setdefault(finding.path, []).append(finding)

    buckets: list[AdvisoryBucket] = []
    for path, path_findings in grouped.items():
        by_kind = Counter(_finding_kind_key(finding) for finding in path_findings)
        total = len(path_findings)
        next_get_session = by_kind.get("next_get_session", 0)
        priority, recommendation = _bucket_priority(total, next_get_session)
        buckets.append(
            {
                "priority": priority,
                "path": path,
                "total": total,
                "depends_get_session": by_kind.get("depends_get_session", 0),
                "next_get_session": next_get_session,
                "recommendation": recommendation,
            }
        )

    return sorted(
        buckets,
        key=lambda bucket: (
            PRIORITY_ORDER[bucket["priority"]],
            -bucket["next_get_session"],
            -bucket["total"],
            bucket["path"],
        ),
    )


def advisory_report(findings: Iterable[SyncSessionFinding]) -> dict[str, object]:
    return {
        "priority_legend": PRIORITY_LEGEND,
        "buckets": advisory_buckets(findings),
        "notes": list(ADVISORY_NOTES),
    }


def _print_text_report(findings: list[SyncSessionFinding], *, summary_only: bool) -> None:
    summary = finding_summary(findings)
    print("Async sync Session audit")
    print("status: advisory")
    print(f"total: {summary['total']}")
    print("by_kind:")
    for kind, count in summary["by_kind"].items():
        print(f"  {kind}: {count}")
    print("by_path:")
    for path, count in summary["by_path"].items():
        print(f"  {path}: {count}")
    print("migration_priority:")
    for bucket in advisory_buckets(findings):
        print(
            "  "
            f"{bucket['priority']} {bucket['path']}: "
            f"total={bucket['total']}, "
            f"depends_get_session={bucket['depends_get_session']}, "
            f"next_get_session={bucket['next_get_session']}"
        )
        print(f"    recommendation: {bucket['recommendation']}")
    print("notes:")
    for note in ADVISORY_NOTES:
        print(f"  {note}")
    if summary_only:
        print("findings: omitted (--summary)")
    else:
        print("findings:")
        for finding in findings:
            print(f"  {finding.key}")


def _print_markdown_report(findings: list[SyncSessionFinding], *, summary_only: bool) -> None:
    summary = finding_summary(findings)
    print("# Async Sync Session Audit")
    print()
    print("Status: advisory. This report is not a blocking gate.")
    print()
    print("## Summary")
    print()
    print(f"- total: {summary['total']}")
    for kind, count in summary["by_kind"].items():
        print(f"- {kind}: {count}")
    print()
    print("## By Path")
    print()
    print("| path | findings |")
    print("| --- | ---: |")
    for path, count in summary["by_path"].items():
        print(f"| `{path}` | {count} |")
    print()
    print("## Migration Priority")
    print()
    print("| priority | path | total | depends_get_session | next_get_session | recommendation |")
    print("| --- | --- | ---: | ---: | ---: | --- |")
    for bucket in advisory_buckets(findings):
        print(
            f"| {bucket['priority']} | `{bucket['path']}` | {bucket['total']} | "
            f"{bucket['depends_get_session']} | {bucket['next_get_session']} | "
            f"{bucket['recommendation']} |"
        )
    print()
    print("## Notes")
    print()
    for note in ADVISORY_NOTES:
        print(f"- {note}")

    if summary_only:
        return

    print()
    print("## Findings")
    print()
    for finding in findings:
        print(f"- `{finding.key}`")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend-root",
        type=Path,
        default=BACKEND_ROOT,
        help="apps/api root to scan",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument("--json", action="store_true", help="emit JSON instead of text")
    output_group.add_argument("--markdown", action="store_true", help="emit Markdown instead of text")
    parser.add_argument("--summary", action="store_true", help="omit individual findings")
    args = parser.parse_args(argv)

    backend_root = args.backend_root.resolve()
    findings = iter_sync_session_findings(backend_root)
    if args.json:
        payload = {
            "summary": finding_summary(findings),
            "advisory": advisory_report(findings),
        }
        if not args.summary:
            payload["findings"] = [asdict(finding) | {"key": finding.key} for finding in findings]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.markdown:
        _print_markdown_report(findings, summary_only=args.summary)
    else:
        _print_text_report(findings, summary_only=args.summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
