from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "sync_profile_runtime_config.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("sync_profile_runtime_config_under_test", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_preserves_runtime_model_and_providers_but_updates_tool_governance(tmp_path: Path) -> None:
    module = _load_module()
    source = {
        "model": {"default": "source-model", "provider": "source-provider"},
        "providers": {"source": {"base_url": "https://source.invalid"}},
        "fallback_providers": [{"provider": "source-fallback"}],
        "custom_providers": [{"name": "Source Custom"}],
        "toolsets": ["terminal", "file", "web", "skills"],
        "skills": {"creation_nudge_interval": 0},
        "agent": {
            "max_turns": 80,
            "tool_use_enforcement": True,
            "disabled_toolsets": ["browser", "memory"],
        },
        "terminal": {"cwd": "/source/project"},
    }
    runtime = {
        "model": {"default": "live-model", "provider": "live-provider"},
        "providers": {"live": {"base_url": "https://live.invalid"}},
        "fallback_providers": [{"provider": "live-fallback"}],
        "custom_providers": [{"name": "Live Custom"}],
        "toolsets": ["terminal"],
        "agent": {
            "max_turns": 144,
            "gateway_timeout": 999,
            "tool_use_enforcement": False,
            "disabled_toolsets": ["skills"],
        },
        "terminal": {"cwd": "/live/project"},
    }
    source_path = tmp_path / "source.yaml"
    runtime_path = tmp_path / "runtime.yaml"
    source_path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")
    runtime_path.write_text(yaml.safe_dump(runtime, sort_keys=False), encoding="utf-8")

    module.sync_runtime_config(source_path, runtime_path)

    merged = yaml.safe_load(runtime_path.read_text(encoding="utf-8"))
    for key in ("model", "providers", "fallback_providers", "custom_providers"):
        assert merged[key] == runtime[key]
    assert merged["toolsets"] == source["toolsets"]
    assert merged["skills"] == source["skills"]
    assert merged["agent"]["tool_use_enforcement"] is True
    assert merged["agent"]["disabled_toolsets"] == ["browser", "memory"]
    assert merged["agent"]["max_turns"] == 144
    assert merged["agent"]["gateway_timeout"] == 999
    assert merged["terminal"] == runtime["terminal"]


def test_sync_initializes_missing_runtime_config_from_source(tmp_path: Path) -> None:
    module = _load_module()
    source = {
        "model": {"default": "source-model"},
        "toolsets": ["terminal", "skills"],
        "skills": {"creation_nudge_interval": 0},
        "agent": {
            "tool_use_enforcement": True,
            "disabled_toolsets": ["browser"],
        },
    }
    source_path = tmp_path / "source.yaml"
    runtime_path = tmp_path / "runtime.yaml"
    source_path.write_text(yaml.safe_dump(source, sort_keys=False), encoding="utf-8")

    module.sync_runtime_config(source_path, runtime_path)

    assert yaml.safe_load(runtime_path.read_text(encoding="utf-8")) == source
