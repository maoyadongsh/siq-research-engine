import importlib.util
from pathlib import Path

import pytest
import yaml


def load_module():
    path = Path(__file__).resolve().parents[1] / "meeting_targets.py"
    spec = importlib.util.spec_from_file_location("meeting_targets", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


def _write_profile(root: Path, name: str, config: dict):
    profile = root / name
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False),
        encoding="utf-8",
    )


def test_discovery_is_read_only_and_deduplicates_custom_provider_aliases(tmp_path):
    module = load_module()
    profiles = tmp_path / "profiles"
    _write_profile(
        profiles,
        "assistant",
        {
            "model": {
                "default": "nemotron_3_nano_omni",
                "provider": "custom:nemotron-short",
                "base_url": "http://127.0.0.1:8007/v1",
            },
            "custom_providers": [
                {
                    "name": "Nemotron 3 Nano Omni Local",
                    "base_url": "http://127.0.0.1:8007/v1",
                    "model": "nemotron_3_nano_omni",
                    "api_mode": "openai_chat",
                    "context_length": 262144,
                }
            ],
        },
    )
    before = (profiles / "assistant" / "config.yaml").read_bytes()

    candidates = module.discover_candidates(profiles)

    assert len(candidates) == 1
    assert candidates[0]["model"] == "nemotron_3_nano_omni"
    assert candidates[0]["provider_name"] == "Nemotron 3 Nano Omni Local"
    assert candidates[0]["context_length"] == 262144
    assert (profiles / "assistant" / "config.yaml").read_bytes() == before


def test_discovery_deduplicates_builtin_provider_and_prefers_explicit_endpoint(tmp_path):
    module = load_module()
    profiles = tmp_path / "profiles"
    _write_profile(
        profiles,
        "kimi-explicit",
        {
            "model": {
                "default": "kimi-for-coding",
                "provider": "kimi-for-coding",
                "provider_name": "Kimi for Coding",
                "base_url": "https://api.kimi.com/coding",
                "api_mode": "openai_chat",
            }
        },
    )
    _write_profile(
        profiles,
        "kimi-provider-only",
        {"model": {"default": "kimi-for-coding", "provider": "kimi-for-coding"}},
    )

    candidates = module.discover_candidates(profiles)

    assert len(candidates) == 1
    assert candidates[0]["provider"] == "kimi-for-coding"
    assert candidates[0]["model"] == "kimi-for-coding"
    assert candidates[0]["base_url"] == "https://api.kimi.com/coding"


def test_build_targets_uses_stable_opaque_refs_and_explicit_allowlist():
    module = load_module()
    candidates = [
        {
            "provider": "custom:local-a",
            "model": "model-a",
            "provider_name": "Local A",
            "base_url": "http://127.0.0.1:8007/v1",
            "api_mode": "openai_chat",
            "key_env": "",
            "context_length": 131072,
            "temperature": 0.2,
        },
        {
            "provider": "cloud-b",
            "model": "model-b",
            "provider_name": "Cloud B",
            "base_url": "https://provider.invalid/v1",
            "api_mode": "openai_chat",
            "key_env": "CLOUD_B_KEY",
            "context_length": 200000,
            "temperature": 0.2,
        },
    ]

    first = module.build_targets(candidates, port_base=18710, allowlist={"model-a"})
    second = module.build_targets(candidates, port_base=18710, allowlist={"model-a"})

    assert first == second
    assert len(first) == 1
    assert first[0]["model_ref"].startswith("meeting:model-a:")
    assert first[0]["locality"] == "local"
    assert first[0]["runs_url"] == "http://127.0.0.1:18710/v1/runs"
    assert not ({"api_key", "token", "secret", "password"} & set(first[0]))
    assert first[0]["api_key_env"] == "SIQ_MEETINGS_HERMES_API_KEY"


def test_builtin_kimi_target_declares_only_its_provider_key_name():
    module = load_module()
    target = module.build_targets(
        [
            {
                "provider": "kimi-coding",
                "model": "kimi-for-coding",
                "provider_name": "Kimi for Coding",
                "base_url": "https://api.kimi.com/coding",
                "api_mode": "openai_chat",
                "key_env": "",
                "context_length": 131072,
                "temperature": 0.2,
            }
        ],
        port_base=18710,
        allowlist=set(),
    )[0]

    assert target["runtime"]["provider_key_env"] == "KIMI_API_KEY"
    assert "credential-value" not in str(target)


def test_builtin_minimax_target_declares_cn_provider_key_name():
    module = load_module()
    target = module.build_targets(
        [
            {
                "provider": "minimax-cn",
                "model": "MiniMax-M3",
                "provider_name": "MiniMax China",
                "base_url": "",
                "api_mode": "",
                "key_env": "",
                "context_length": 204800,
                "temperature": 0.2,
            }
        ],
        port_base=18710,
        allowlist=set(),
    )[0]

    assert target["runtime"]["provider_key_env"] == "MINIMAX_CN_API_KEY"
    assert "credential-value" not in str(target)


def test_provider_credential_bridge_loads_only_required_key(tmp_path):
    module = load_module()
    credential_file = tmp_path / ".env"
    credential_file.write_text(
        "UNRELATED_PROVIDER_KEY=must-not-be-loaded\nKIMI_API_KEY=test-kimi-credential-value\n",
        encoding="utf-8",
    )
    credential_file.chmod(0o600)
    target = {"runtime": {"provider_key_env": "KIMI_API_KEY"}}
    env = {"PATH": "/usr/bin"}

    loaded_key = module._bridge_provider_credential(
        target,
        env,
        credential_files=[credential_file],
    )

    assert loaded_key == "KIMI_API_KEY"
    assert env["KIMI_API_KEY"] == "test-kimi-credential-value"
    assert "UNRELATED_PROVIDER_KEY" not in env
    assert "test-kimi-credential-value" not in str(target)


def test_provider_credential_bridge_fails_closed_when_key_is_missing(tmp_path):
    module = load_module()
    credential_file = tmp_path / ".env"
    credential_file.write_text("OTHER_KEY=value\n", encoding="utf-8")
    credential_file.chmod(0o600)

    with pytest.raises(module.TargetConfigurationError, match="required provider credential"):
        module._bridge_provider_credential(
            {"runtime": {"provider_key_env": "KIMI_API_KEY"}},
            {},
            credential_files=[credential_file],
        )


def test_provider_credential_bridge_rejects_broad_file_permissions(tmp_path):
    module = load_module()
    credential_file = tmp_path / ".env"
    credential_file.write_text("KIMI_API_KEY=test-value\n", encoding="utf-8")
    credential_file.chmod(0o644)

    with pytest.raises(module.TargetConfigurationError, match="permissions are too broad"):
        module._bridge_provider_credential(
            {"runtime": {"provider_key_env": "KIMI_API_KEY"}},
            {},
            credential_files=[credential_file],
        )


def test_rendered_target_has_no_tools_or_fallbacks():
    module = load_module()
    target = module.build_targets(
        [
            {
                "provider": "custom:nemotron-local",
                "model": "nemotron",
                "provider_name": "Nemotron Local",
                "base_url": "http://127.0.0.1:8007/v1",
                "api_mode": "openai_chat",
                "key_env": "",
                "context_length": 262144,
                "temperature": 0.2,
            }
        ],
        port_base=18710,
        allowlist=set(),
    )[0]

    config = module._target_config(target, 18710)

    assert config["fallback_providers"] == []
    assert config["toolsets"] == []
    assert config["agent"]["max_turns"] == 2
    assert config["model"]["provider"] == "custom:nemotron-local"
    assert config["custom_providers"][0]["name"] == "nemotron-local"
    assert set(config["agent"]["disabled_toolsets"]) >= {
        "terminal",
        "file",
        "code_execution",
        "browser",
        "web",
    }
    assert config["memory"]["memory_enabled"] is False


def test_rendered_custom_provider_uses_canonical_slug_when_display_name_has_punctuation():
    module = load_module()
    target = module.build_targets(
        [
            {
                "provider": "custom:stepfun-step-3-7-flash",
                "model": "step-3.7-flash",
                "provider_name": "StepFun Step-3.7 Flash",
                "base_url": "https://api.stepfun.com/v1",
                "api_mode": "openai_chat",
                "key_env": "SIQ_STEPFUN_LLM_API_KEY",
                "context_length": 200000,
                "temperature": 0.2,
            }
        ],
        port_base=18710,
        allowlist=set(),
    )[0]

    config = module._target_config(target, 18710)

    assert config["model"]["provider"] == "custom:stepfun-step-3-7-flash"
    assert config["custom_providers"][0]["name"] == "stepfun-step-3-7-flash"


def test_inline_profile_credential_is_rejected(tmp_path):
    module = load_module()
    profiles = tmp_path / "profiles"
    _write_profile(
        profiles,
        "unsafe",
        {
            "model": {
                "default": "unsafe-model",
                "provider": "unsafe-provider",
                "api_key": "must-not-be-copied",
            }
        },
    )

    with pytest.raises(module.TargetConfigurationError, match="inline credential"):
        module.discover_candidates(profiles)
