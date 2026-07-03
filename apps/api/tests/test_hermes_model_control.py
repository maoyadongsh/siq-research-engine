import yaml

from services import hermes_model_control as control


def _write_config(path, *, model=None):
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
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _patch_profile(monkeypatch, path):
    monkeypatch.setitem(control.PROFILE_CONFIGS, "siq_assistant", path)


def _read_config(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


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
