#!/usr/bin/env python3
"""Merge source-owned Hermes tool governance into a live profile config."""

from __future__ import annotations

import argparse
import copy
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

import yaml


GOVERNED_TOP_LEVEL_KEYS = ("toolsets", "skills")
GOVERNED_AGENT_KEYS = ("tool_use_enforcement", "disabled_toolsets")


def _read_mapping(path: Path) -> dict[str, Any]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"config not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML config: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"config must be a YAML mapping: {path}")
    return payload


def merge_tool_governance(
    source: dict[str, Any],
    runtime: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a runtime config with source tool policy and runtime model/provider state."""

    if runtime is None:
        return copy.deepcopy(source)

    merged = copy.deepcopy(runtime)
    for key in GOVERNED_TOP_LEVEL_KEYS:
        if key not in source:
            raise ValueError(f"source config is missing governed key: {key}")
        merged[key] = copy.deepcopy(source[key])

    source_agent = source.get("agent")
    runtime_agent = merged.get("agent")
    if not isinstance(source_agent, dict):
        raise ValueError("source config is missing governed mapping: agent")
    if runtime_agent is None:
        runtime_agent = {}
        merged["agent"] = runtime_agent
    if not isinstance(runtime_agent, dict):
        raise ValueError("runtime config field must be a mapping: agent")
    for key in GOVERNED_AGENT_KEYS:
        if key not in source_agent:
            raise ValueError(f"source config is missing governed key: agent.{key}")
        runtime_agent[key] = copy.deepcopy(source_agent[key])

    return merged


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    existing_mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o600
    serialized = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_path, existing_mode)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def sync_runtime_config(source_path: Path, runtime_path: Path) -> dict[str, Any]:
    source = _read_mapping(source_path)
    runtime = _read_mapping(runtime_path) if runtime_path.exists() else None
    merged = merge_tool_governance(source, runtime)
    _atomic_write_yaml(runtime_path, merged)
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sync Hermes tool/skill governance while preserving runtime model/provider settings."
    )
    parser.add_argument("source_config", type=Path)
    parser.add_argument("runtime_config", type=Path)
    args = parser.parse_args()
    try:
        sync_runtime_config(args.source_config, args.runtime_config)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
