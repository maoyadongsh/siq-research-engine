from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from scripts.openshell.build_siq_analysis_runtime_config import (
    DEFAULT_PROJECT_ROOT,
    RuntimeConfigError,
    compile_runtime_config,
    main,
)


def _config() -> dict:
    return {
        "model": {"provider": "minimax-cn", "default": "MiniMax-M3"},
        "fallback_providers": [
            {
                "provider": "custom:stepfun-step-3.7-flash",
                "model": "step-3.7-flash",
                "base_url": "https://api.stepfun.com/v1",
                "key_env": "SIQ_STEPFUN_LLM_API_KEY",
            },
            {
                "provider": "custom:qwen3.6-local",
                "model": "Qwen3.6-35B-A3B-FP8",
                "base_url": "http://127.0.0.1:8004/v1",
            },
            {
                "provider": "custom:gemma4-local",
                "model": "Gemma-4-26B-A4B-it-NVFP4",
                "base_url": "http://localhost:8006/v1",
            },
        ],
        "custom_providers": [
            {
                "name": "Qwen local",
                "model": "Qwen3.6-35B-A3B-FP8",
                "base_url": "http://127.0.0.1:8004/v1",
            }
        ],
        "toolsets": ["terminal", "file", "code_execution", "web"],
        "terminal": {
            "backend": "local",
            "cwd": "/home/maoyd",
            "env_passthrough": [
                "PATH",
                "HOME",
                "TAVILY_API_KEY",
                "EXA_API_KEY",
                "UNREVIEWED_API_KEY",
            ],
            "shell_init_files": ["~/.bashrc"],
            "auto_source_bashrc": True,
            "persistent_shell": True,
        },
        "security": {"redact_secrets": False},
        "browser": {"allow_private_urls": True},
        "platforms": {
            "api_server": {
                "enabled": True,
                "key": "",
                "extra": {"host": "0.0.0.0", "port": 18651, "model_name": "old"},
            }
        },
        "webhook": {"secret": ""},
    }


def _compile(payload: dict) -> tuple[dict, dict]:
    content, summary_content, _ = compile_runtime_config(payload, source_sha256="a" * 64)
    return yaml.safe_load(content), json.loads(summary_content)


def test_compiler_preserves_route_order_and_rewrites_only_loopback_services() -> None:
    compiled, summary = _compile(_config())

    expected_order = [
        ("minimax-cn", "MiniMax-M3"),
        ("custom:stepfun-step-3.7-flash", "step-3.7-flash"),
        ("custom:qwen3.6-local", "Qwen3.6-35B-A3B-FP8"),
        ("custom:gemma4-local", "Gemma-4-26B-A4B-it-NVFP4"),
    ]
    assert [(item["provider"], item["model"]) for item in summary["source_routes"]] == expected_order
    assert [(item["provider"], item["model"]) for item in summary["routes"]] == expected_order
    assert summary["source_routes"][2]["host"] == "127.0.0.1"
    assert summary["routes"][2]["host"] == "host.openshell.internal"
    assert summary["loopback_rewrites"] == [
        {"port": 8004, "target_host": "host.openshell.internal"},
        {"port": 8006, "target_host": "host.openshell.internal"},
    ]
    assert summary["loopback_rewrite_occurrences"] == 3
    assert compiled["fallback_providers"][0]["base_url"] == "https://api.stepfun.com/v1"
    assert compiled["fallback_providers"][1]["base_url"] == "http://host.openshell.internal:8004/v1"
    assert compiled["fallback_providers"][2]["base_url"] == "http://host.openshell.internal:8006/v1"
    assert compiled["custom_providers"][0]["base_url"] == "http://host.openshell.internal:8004/v1"


def test_compiler_hardens_terminal_and_api_without_changing_toolsets() -> None:
    compiled, summary = _compile(_config())

    assert compiled["toolsets"] == ["terminal", "file", "code_execution", "web"]
    assert compiled["terminal"]["cwd"] == DEFAULT_PROJECT_ROOT
    assert compiled["terminal"]["shell_init_files"] == []
    assert compiled["terminal"]["auto_source_bashrc"] is False
    assert compiled["terminal"]["env_passthrough"] == [
        "PATH",
        "HOME",
        "TAVILY_API_KEY",
        "EXA_API_KEY",
        "KIMI_API_KEY",
        "SIQ_MINIMAX_CN_BACKUP",
        "SIQ_MINIMAX_CN_PRIMARY",
        "SIQ_PG_QUERY_BROKER_URL",
        "SIQ_STEPFUN_LLM_API_KEY",
    ]
    assert compiled["security"]["redact_secrets"] is True
    assert compiled["browser"]["allow_private_urls"] is False
    assert compiled["platforms"]["api_server"]["key"] == ""
    assert compiled["platforms"]["api_server"]["extra"] == {
        "host": "127.0.0.1",
        "port": 28651,
        "model_name": "siq_analysis",
    }
    assert summary["terminal_env_removed"] == ["UNREVIEWED_API_KEY"]
    assert summary["terminal_provider_placeholder_env"] == [
        "EXA_API_KEY",
        "KIMI_API_KEY",
        "SIQ_MINIMAX_CN_BACKUP",
        "SIQ_MINIMAX_CN_PRIMARY",
        "SIQ_STEPFUN_LLM_API_KEY",
        "TAVILY_API_KEY",
    ]
    assert summary["terminal_broker_env"] == ["SIQ_PG_QUERY_BROKER_URL"]
    assert summary["inline_secret_values"] is False


def test_compiler_rejects_inline_secrets_unknown_loopback_and_route_duplicates() -> None:
    inline = _config()
    inline["model"]["api_key"] = "must-not-enter-image"
    with pytest.raises(RuntimeConfigError, match="inline secret"):
        _compile(inline)

    unknown = _config()
    unknown["fallback_providers"][1]["base_url"] = "http://127.0.0.1:9999/v1"
    with pytest.raises(RuntimeConfigError, match="unsupported loopback"):
        _compile(unknown)

    duplicate = _config()
    duplicate["fallback_providers"].append(copy.deepcopy(duplicate["fallback_providers"][1]))
    with pytest.raises(RuntimeConfigError, match="duplicate"):
        _compile(duplicate)


def test_cli_writes_mode_600_and_check_detects_drift(tmp_path: Path) -> None:
    source = tmp_path / "config.yaml"
    output = tmp_path / "compiled.yaml"
    summary = tmp_path / "summary.json"
    source.write_text(yaml.safe_dump(_config(), sort_keys=False), encoding="utf-8")

    assert main(["--input", str(source), "--output", str(output), "--summary-output", str(summary)]) == 0
    assert output.stat().st_mode & 0o777 == 0o600
    assert summary.stat().st_mode & 0o777 == 0o600
    assert (
        json.loads(summary.read_text(encoding="utf-8"))["source_sha256"]
        == hashlib.sha256(source.read_bytes()).hexdigest()
    )
    assert main(["--input", str(source), "--output", str(output), "--summary-output", str(summary), "--check"]) == 0

    output.write_text("stale\n", encoding="utf-8")
    assert main(["--input", str(source), "--output", str(output), "--summary-output", str(summary), "--check"]) == 2
