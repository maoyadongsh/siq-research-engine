#!/usr/bin/env python3
"""Audit which API test files are included in CI's execution surface."""

from __future__ import annotations

import argparse
import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_WORKFLOWS = (Path(".github/workflows/ci.yml"),)
API_TEST_ROOT = Path("apps/api/tests")
SLOW_NETWORK_MARKERS = {"slow", "network"}
TEST_FILE_REF_PATTERN = re.compile(r"(?<![\w./-])(?:apps/api/)?tests/test_[A-Za-z0-9_]+\.py\b")


@dataclass(frozen=True)
class ApiCiCoverageResult:
    status: str
    passed: bool
    total_test_files: int
    covered_files: list[str]
    excluded_slow_network_files: list[str]
    uncovered_files: list[str]
    workflow_files: list[str]
    messages: list[str]


def _repo_relative(path: Path, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _normalize_test_ref(value: str) -> str:
    text = value.strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if text.startswith("tests/"):
        text = f"apps/api/{text}"
    return text


def discover_api_test_files(repo_root: Path) -> list[str]:
    test_root = repo_root / API_TEST_ROOT
    if not test_root.exists():
        return []
    return sorted(_repo_relative(path, repo_root) for path in test_root.glob("test_*.py") if path.is_file())


def ci_covered_api_test_files(repo_root: Path, workflows: Iterable[Path]) -> list[str]:
    covered: set[str] = set()
    for workflow in workflows:
        path = workflow if workflow.is_absolute() else repo_root / workflow
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if _runs_full_api_test_directory(text):
            covered.update(discover_api_test_files(repo_root))
            continue
        for match in TEST_FILE_REF_PATTERN.finditer(text):
            covered.add(_normalize_test_ref(match.group(0)))
    return sorted(covered)


def _runs_full_api_test_directory(workflow_text: str) -> bool:
    lines = workflow_text.splitlines()
    in_api_working_dir = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("working-directory:"):
            in_api_working_dir = stripped.split(":", 1)[1].strip().strip("'\"") == "apps/api"
            continue
        if not in_api_working_dir:
            continue
        if re.search(r"\bpytest\b", stripped) and re.search(r"(^|\s)tests(\s|$)", stripped):
            return True
    return bool(re.search(r"\bpytest\b[^\n]*\sapps/api/tests(?:\s|$)", workflow_text))


def _marker_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Call):
        return _marker_name(node.func)
    if isinstance(node, ast.Attribute):
        if isinstance(node.value, ast.Attribute) and isinstance(node.value.value, ast.Name):
            if node.value.value.id == "pytest" and node.value.attr == "mark":
                return node.attr
    return None


def _marker_names(node: ast.AST) -> set[str]:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        names: set[str] = set()
        for item in node.elts:
            marker = _marker_name(item)
            if marker:
                names.add(marker)
        return names
    marker = _marker_name(node)
    return {marker} if marker else set()


def _has_slow_network_marker(node: ast.AST) -> bool:
    decorators = getattr(node, "decorator_list", [])
    return any((_marker_name(decorator) or "") in SLOW_NETWORK_MARKERS for decorator in decorators)


def is_slow_network_only_test_file(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError:
        return False

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets):
            continue
        if _marker_names(node.value).intersection(SLOW_NETWORK_MARKERS):
            return True

    test_nodes = [
        node
        for node in tree.body
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name.startswith("test_")
        )
        or (isinstance(node, ast.ClassDef) and node.name.startswith("Test"))
    ]
    return bool(test_nodes) and all(_has_slow_network_marker(node) for node in test_nodes)


def audit_api_ci_test_coverage(
    repo_root: Path,
    *,
    workflows: Iterable[Path] = DEFAULT_WORKFLOWS,
    fail_on_uncovered: bool = False,
) -> tuple[int, ApiCiCoverageResult]:
    repo_root = repo_root.resolve()
    all_files = discover_api_test_files(repo_root)
    covered = set(ci_covered_api_test_files(repo_root, workflows))
    excluded = {
        relative
        for relative in all_files
        if is_slow_network_only_test_file(repo_root / relative)
    }
    required = [relative for relative in all_files if relative not in excluded]
    uncovered = sorted(relative for relative in required if relative not in covered)
    workflow_files = [
        _repo_relative(workflow if workflow.is_absolute() else repo_root / workflow, repo_root)
        for workflow in workflows
    ]

    messages: list[str] = [
        "API CI execution audit checks apps/api/tests/test_*.py files against workflow pytest commands.",
        "Files marked slow/network at file level or all top-level tests are excluded from the required set.",
    ]
    if uncovered:
        messages.append(
            f"Found {len(uncovered)} non slow/network API test file(s) not named in CI; "
            "this run is advisory unless --fail-on-uncovered is set."
        )
    else:
        messages.append("All non slow/network API test files are named in CI.")

    status = "passed"
    exit_code = 0
    if uncovered:
        status = "failed" if fail_on_uncovered else "advisory"
        exit_code = 1 if fail_on_uncovered else 0

    result = ApiCiCoverageResult(
        status=status,
        passed=not uncovered,
        total_test_files=len(all_files),
        covered_files=sorted(relative for relative in covered if relative in all_files),
        excluded_slow_network_files=sorted(excluded),
        uncovered_files=uncovered,
        workflow_files=workflow_files,
        messages=messages,
    )
    return exit_code, result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit API CI test-file execution coverage.")
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    parser.add_argument(
        "--workflow",
        action="append",
        type=Path,
        dest="workflows",
        help="Workflow file to scan. May be repeated. Defaults to .github/workflows/ci.yml.",
    )
    parser.add_argument("--fail-on-uncovered", action="store_true", help="Return non-zero when uncovered files exist.")
    parser.add_argument("--json", action="store_true", help="Emit JSON summary.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    exit_code, result = audit_api_ci_test_coverage(
        args.repo_root,
        workflows=tuple(args.workflows or DEFAULT_WORKFLOWS),
        fail_on_uncovered=args.fail_on_uncovered,
    )
    if args.json:
        print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    else:
        print(f"{result.status.upper()} API CI test coverage audit")
        for message in result.messages:
            print(message)
        print(f"Total API test files: {result.total_test_files}")
        print(f"Covered by CI execution: {len(result.covered_files)}")
        print(f"Excluded slow/network files: {len(result.excluded_slow_network_files)}")
        print(f"Uncovered non slow/network files: {len(result.uncovered_files)}")
        if result.uncovered_files:
            print("Uncovered files:")
            for relative in result.uncovered_files:
                print(f"- {relative}")
    return exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
