#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(__file__).with_name("mypy_whitelist.toml")


@dataclass(frozen=True)
class MypyTarget:
    name: str
    paths: tuple[str, ...]


@dataclass(frozen=True)
class MypyWhitelistConfig:
    config_file: str
    targets: tuple[MypyTarget, ...]


@dataclass
class MypyWhitelistResult:
    status: str
    selected_targets: list[str]
    checked_paths: list[str]
    commands: list[list[str]] = field(default_factory=list)
    mypy_version: str | None = None
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    messages: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_whitelist_config(path: Path) -> MypyWhitelistConfig:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    settings = payload.get("settings")
    if not isinstance(settings, dict):
        raise ValueError("mypy whitelist config must define [settings]")

    config_file = settings.get("config_file")
    if not isinstance(config_file, str) or not config_file.strip():
        raise ValueError("mypy whitelist config must define settings.config_file")

    raw_targets = payload.get("target")
    if not isinstance(raw_targets, list) or not raw_targets:
        raise ValueError("mypy whitelist config must define at least one [[target]]")

    targets: list[MypyTarget] = []
    seen: set[str] = set()
    for raw_target in raw_targets:
        if not isinstance(raw_target, dict):
            raise ValueError("each mypy whitelist target must be a table")
        name = raw_target.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("each mypy whitelist target must define a name")
        if name in seen:
            raise ValueError(f"duplicate mypy whitelist target: {name}")
        seen.add(name)

        paths = raw_target.get("paths")
        if not isinstance(paths, list) or not paths:
            raise ValueError(f"mypy whitelist target {name!r} must define paths")
        normalized_paths: list[str] = []
        for item in paths:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"mypy whitelist target {name!r} contains an invalid path")
            normalized_paths.append(item)
        targets.append(MypyTarget(name=name, paths=tuple(normalized_paths)))

    return MypyWhitelistConfig(config_file=config_file, targets=tuple(targets))


def _selected_targets(config: MypyWhitelistConfig, selected_names: list[str] | None) -> list[MypyTarget]:
    if not selected_names:
        return list(config.targets)

    by_name = {target.name: target for target in config.targets}
    missing = [name for name in selected_names if name not in by_name]
    if missing:
        raise ValueError(f"unknown mypy whitelist target(s): {', '.join(missing)}")
    return [by_name[name] for name in selected_names]


def _validate_paths(repo_root: Path, config: MypyWhitelistConfig, targets: list[MypyTarget]) -> None:
    config_path = repo_root / config.config_file
    if not config_path.is_file():
        raise ValueError(f"mypy config file does not exist: {config.config_file}")

    missing = [
        path
        for target in targets
        for path in target.paths
        if not (repo_root / path).exists()
    ]
    if missing:
        raise ValueError(f"mypy whitelist path(s) do not exist: {', '.join(missing)}")


def _mypy_version(python_executable: str, repo_root: Path) -> tuple[str | None, str]:
    completed = subprocess.run(
        [python_executable, "-m", "mypy", "--version"],
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode == 0:
        return completed.stdout.strip(), ""
    return None, (completed.stderr or completed.stdout).strip()


def run_mypy_whitelist(
    repo_root: Path,
    *,
    whitelist_config: Path = DEFAULT_CONFIG,
    selected_names: list[str] | None = None,
    python_executable: str = sys.executable,
    require_mypy: bool = False,
) -> tuple[int, MypyWhitelistResult]:
    config = load_whitelist_config(whitelist_config)
    targets = _selected_targets(config, selected_names)
    _validate_paths(repo_root, config, targets)

    selected_target_names = [target.name for target in targets]
    checked_paths = sorted({path for target in targets for path in target.paths})
    result = MypyWhitelistResult(
        status="pending",
        selected_targets=selected_target_names,
        checked_paths=checked_paths,
    )

    version, version_error = _mypy_version(python_executable, repo_root)
    result.mypy_version = version
    if version is None:
        message = "mypy is not installed for the selected Python executable"
        if version_error:
            message = f"{message}: {version_error}"
        result.messages.append(message)
        result.status = "failed" if require_mypy else "advisory"
        return (1 if require_mypy else 0), result

    command = [
        python_executable,
        "-m",
        "mypy",
        "--config-file",
        config.config_file,
        *checked_paths,
    ]
    result.commands.append(command)
    completed = subprocess.run(
        command,
        cwd=repo_root,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    result.returncode = completed.returncode
    result.stdout = completed.stdout
    result.stderr = completed.stderr
    result.status = "passed" if completed.returncode == 0 else "failed"
    return completed.returncode, result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run mypy for the explicit SIQ whitelist targets.")
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--target", action="append", dest="targets")
    parser.add_argument("--python-executable", default=sys.executable)
    parser.add_argument("--require-mypy", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        exit_code, result = run_mypy_whitelist(
            args.repo_root.resolve(),
            whitelist_config=args.config.resolve(),
            selected_names=args.targets,
            python_executable=args.python_executable,
            require_mypy=args.require_mypy,
        )
    except ValueError as exc:
        result = MypyWhitelistResult(
            status="failed",
            selected_targets=args.targets or [],
            checked_paths=[],
            messages=[str(exc)],
        )
        exit_code = 2

    if args.json_output:
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"mypy whitelist status: {result.status}")
        for message in result.messages:
            print(message, file=sys.stderr if exit_code else sys.stdout)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
