import stat

import pytest
import yaml

from services import hermes_model_control as control


def _write_config(path, *, model=None, extra=None):
    data = {
        "model": model
        or {
            "default": control.QWEN36_MODEL,
            "provider": control.QWEN36_PROVIDER,
            "base_url": control.QWEN36_BASE_URL,
            "api_mode": "openai_chat",
            "context_length": control.QWEN36_CONTEXT_LENGTH,
            "temperature": control.QWEN36_TEMPERATURE,
        },
        "agent": {},
    }
    data.update(extra or {})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _patch_profile(monkeypatch, path):
    monkeypatch.setitem(control.PROFILE_CONFIGS, "siq_assistant", path)


def _read_config(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_custom_provider_slugs_match_hermes_name_normalization():
    for mode in ("qwen36", "gemma4", "nemotron", "stepfun"):
        option = control.MODEL_OPTIONS[mode]
        assert option["provider"] == control._custom_provider_slug(option["provider_name"])

    assert control.NEMOTRON_PROVIDER == "custom:nemotron-3-nano-omni-local"


def test_status_reports_qwen36_when_runtime_profile_uses_qwen(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _patch_profile(monkeypatch, config_path)

    reply = control.maybe_handle_model_control("当前模型是什么？", "siq_assistant")

    assert reply is not None
    assert "本地 Qwen3.6" in reply
    assert control.QWEN36_MODEL in reply
    assert control.GEMMA4_MODEL not in reply.split("备用顺序：", 1)[0]


def test_switch_commands_cover_all_models(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _patch_profile(monkeypatch, config_path)

    cases = [
        ("切换到 Kimi", "kimi", control.KIMI_MODEL, control.KIMI_PROVIDER),
        ("切换到 MiniMax", "minimax", control.MINIMAX_MODEL, control.MINIMAX_PROVIDER),
        ("切换到 stepfun", "stepfun", control.STEPFUN_MODEL, control.STEPFUN_PROVIDER),
        ("切换到 Qwen3.6", "qwen36", control.QWEN36_MODEL, control.QWEN36_PROVIDER),
        ("切换到 Gemma4", "gemma4", control.GEMMA4_MODEL, control.GEMMA4_PROVIDER),
        ("切换到 Nemotron", "nemotron", control.NEMOTRON_MODEL, control.NEMOTRON_PROVIDER),
    ]

    for message, mode, model, provider in cases:
        reply = control.maybe_handle_model_control(message, "siq_assistant")
        data = _read_config(config_path)
        assert reply is not None
        assert model in reply
        assert control.current_model_mode("siq_assistant") == mode
        assert data["model"]["default"] == model
        assert data["model"]["provider"] == provider
        assert all(item["model"] != model for item in data["fallback_providers"])


def test_local_alias_switches_to_qwen36_not_gemma4(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        model={
            "default": control.KIMI_MODEL,
            "provider": control.KIMI_PROVIDER,
            "base_url": control.KIMI_BASE_URL,
        },
    )
    _patch_profile(monkeypatch, config_path)

    reply = control.maybe_handle_model_control("切换到本地模型", "siq_assistant")
    data = _read_config(config_path)

    assert reply is not None
    assert "本地 Qwen3.6" in reply
    assert data["model"]["default"] == control.QWEN36_MODEL
    assert data["model"]["provider"] == control.QWEN36_PROVIDER
    assert data["model"]["base_url"] == control.QWEN36_BASE_URL


def test_cloud_alias_switches_to_stepfun(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _patch_profile(monkeypatch, config_path)

    reply = control.maybe_handle_model_control("切换到云端模型", "siq_assistant")
    data = _read_config(config_path)

    assert reply is not None
    assert "云端 StepFun" in reply
    assert data["model"]["default"] == control.STEPFUN_MODEL
    assert data["model"]["provider"] == control.STEPFUN_PROVIDER
    assert data["model"]["base_url"] == control.STEPFUN_BASE_URL
    assert data["fallback_providers"][0]["model"] == control.QWEN36_MODEL


def test_nemotron_switch_registers_custom_provider(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _patch_profile(monkeypatch, config_path)

    reply = control.maybe_handle_model_control("请切换到 NVIDIA Nemotron 3", "siq_assistant")
    data = _read_config(config_path)

    assert reply is not None
    assert "本地 Nemotron 3 Nano Omni" in reply
    assert data["model"] == {
        "default": control.NEMOTRON_MODEL,
        "provider": control.NEMOTRON_PROVIDER,
        "base_url": control.NEMOTRON_BASE_URL,
        "api_mode": "openai_chat",
        "context_length": control.NEMOTRON_CONTEXT_LENGTH,
        "temperature": control.NEMOTRON_TEMPERATURE,
    }
    provider = next(item for item in data["custom_providers"] if item["name"] == control.NEMOTRON_PROVIDER_NAME)
    assert provider["base_url"] == control.NEMOTRON_BASE_URL
    assert provider["model"] == control.NEMOTRON_MODEL
    assert provider["models"][control.NEMOTRON_MODEL]["context_length"] == control.NEMOTRON_CONTEXT_LENGTH
    assert all(item["model"] != control.NEMOTRON_MODEL for item in data["fallback_providers"])


def test_switch_updates_existing_legacy_live_mirror_and_status(tmp_path, monkeypatch):
    profiles_root = tmp_path / "profiles"
    canonical_path = profiles_root / "siq_assistant" / "config.yaml"
    live_path = profiles_root / "finsight_assistant" / "config.yaml"
    _write_config(
        canonical_path,
        extra={"canonical_only": {"preserve": True}},
    )
    _write_config(
        live_path,
        model={
            "default": control.MINIMAX_MODEL,
            "provider": control.MINIMAX_PROVIDER,
        },
        extra={"live_only": {"preserve": True}},
    )
    _patch_profile(monkeypatch, canonical_path)

    before = control.describe_profile_model("siq_assistant")
    assert before["mode"] == "minimax"
    assert before["model"] == control.MINIMAX_MODEL

    reply = control.maybe_handle_model_control("切换到 Nemotron", "siq_assistant")

    assert reply is not None
    assert control.NEMOTRON_MODEL in reply
    for path in (canonical_path, live_path):
        data = _read_config(path)
        assert data["model"]["default"] == control.NEMOTRON_MODEL
        assert data["model"]["provider"] == control.NEMOTRON_PROVIDER
        assert data["model"]["base_url"] == control.NEMOTRON_BASE_URL
        assert all(item["model"] != control.NEMOTRON_MODEL for item in data["fallback_providers"])
    assert _read_config(canonical_path)["canonical_only"] == {"preserve": True}
    assert _read_config(live_path)["live_only"] == {"preserve": True}

    after = control.describe_profile_model("siq_assistant")
    assert after["mode"] == "nemotron"
    assert after["model"] == control.NEMOTRON_MODEL
    assert after["provider"] == control.NEMOTRON_PROVIDER


def test_ensure_fallback_updates_existing_legacy_live_mirror(tmp_path, monkeypatch):
    profiles_root = tmp_path / "profiles"
    canonical_path = profiles_root / "siq_assistant" / "config.yaml"
    live_path = profiles_root / "finsight_assistant" / "config.yaml"
    _write_config(canonical_path, extra={"fallback_providers": []})
    _write_config(live_path, extra={"fallback_providers": []})
    _patch_profile(monkeypatch, canonical_path)

    control.ensure_profile_fallback("siq_assistant")

    expected = control._fallback_chain_for_mode("qwen36")
    for path in (canonical_path, live_path):
        data = _read_config(path)
        assert data["fallback_providers"] == expected
        assert data["agent"]["tool_use_enforcement"] is True


def test_save_yaml_replaces_atomically_and_preserves_mode(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    config_path.chmod(0o640)
    replace_calls = []
    real_replace = control.os.replace

    def recording_replace(source, target):
        replace_calls.append((source, target))
        assert source.parent == config_path.parent
        assert target == config_path
        real_replace(source, target)

    monkeypatch.setattr(control.os, "replace", recording_replace)

    control._save_yaml(config_path, {"model": {"default": "atomic-model"}, "preserved": True})

    assert len(replace_calls) == 1
    assert _read_config(config_path)["preserved"] is True
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o640
    assert list(tmp_path.glob(".config.yaml.*.tmp")) == []


def test_model_list_includes_local_and_cloud_options(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _patch_profile(monkeypatch, config_path)

    reply = control.maybe_handle_model_control("有哪些可用模型？", "siq_assistant")

    assert reply is not None
    for mode in control.CANONICAL_MODEL_MODES:
        assert control.MODEL_OPTIONS[mode]["label"] in reply


def test_infer_model_mode_recognizes_nemotron_endpoint():
    assert (
        control.infer_model_mode(
            provider_name="本地 vLLM / Nemotron 3 Nano Omni",
            model=control.NEMOTRON_MODEL,
            base_url=control.NEMOTRON_BASE_URL,
        )
        == "nemotron"
    )


def test_status_question_mentioning_gemma4_does_not_switch(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _patch_profile(monkeypatch, config_path)

    reply = control.maybe_handle_model_control("当前使用的是 Gemma4 吗？", "siq_assistant")
    data = _read_config(config_path)

    assert reply is not None
    assert "当前使用本地 Qwen3.6" in reply
    assert data["model"]["default"] == control.QWEN36_MODEL
    assert data["model"]["provider"] == control.QWEN36_PROVIDER


def test_model_catalog_is_safe_and_apply_rejects_unknown_modes(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    _write_config(config_path)
    _patch_profile(monkeypatch, config_path)

    catalog = control.model_catalog()

    assert [item["mode"] for item in catalog["options"]] == list(control.CANONICAL_MODEL_MODES)
    assert all(set(item) == {"mode", "label", "kind", "model", "provider"} for item in catalog["options"])
    assert "base_url" not in str(catalog)
    assert catalog["profiles"]["siq_assistant"]["mode"] == "qwen36"

    with pytest.raises(ValueError, match="Unsupported Hermes model mode"):
        control.apply_profile_model_mode("siq_assistant", "unconfigured-model")
