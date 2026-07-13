import asyncio
import json

from services import llm_settings


def test_load_llm_settings_reads_legacy_backend_config_when_new_config_missing(tmp_path, monkeypatch):
    new_config = tmp_path / "data" / "backend" / ".siq" / "llm_settings.json"
    legacy_config = tmp_path / "apps" / "api" / ".siq" / "llm_settings.json"
    legacy_config.parent.mkdir(parents=True)
    legacy_config.write_text(
        json.dumps(
            {
                "activeProvider": "cloud",
                "providers": {
                    "cloud": {
                        "enabled": True,
                        "providerName": "Hermes / Kimi",
                        "baseUrl": "hermes://kimi-coding",
                        "apiKey": "secret",
                        "model": "kimi-for-coding",
                        "temperature": 0.1,
                        "maxTokens": 1234,
                        "timeoutSeconds": 77,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(llm_settings, "CONFIG_PATH", new_config)
    monkeypatch.setattr(llm_settings, "LEGACY_CONFIG_PATHS", (legacy_config,))

    loaded = llm_settings.load_llm_settings(include_secrets=True)

    assert loaded["activeProvider"] == "cloud"
    assert loaded["providers"]["cloud"]["providerName"] == "Hermes / Minimax"
    assert loaded["providers"]["cloud"]["baseUrl"] == "hermes://minimax-cn"
    assert loaded["providers"]["cloud"]["model"] == "MiniMax-M3"
    assert loaded["providers"]["cloud"]["apiKey"] == ""
    assert loaded["providers"]["cloud"]["maxTokens"] == 1234


def test_settings_source_path_prefers_new_config(tmp_path, monkeypatch):
    new_config = tmp_path / "data" / "backend" / ".siq" / "llm_settings.json"
    legacy_config = tmp_path / "apps" / "api" / ".siq" / "llm_settings.json"
    new_config.parent.mkdir(parents=True)
    legacy_config.parent.mkdir(parents=True)
    new_config.write_text("{}", encoding="utf-8")
    legacy_config.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(llm_settings, "CONFIG_PATH", new_config)
    monkeypatch.setattr(llm_settings, "LEGACY_CONFIG_PATHS", (legacy_config,))

    assert llm_settings._settings_source_path() == new_config


def test_nemotron_is_exposed_as_local_model_preset():
    presets = llm_settings._public_local_model_presets()

    assert set(presets) == {"qwen36", "gemma4", "nemotron"}
    assert presets["nemotron"]["providerName"] == "本地 vLLM / Nemotron 3 Nano Omni"
    assert presets["nemotron"]["baseUrl"] == "http://127.0.0.1:8007/v1"
    assert presets["nemotron"]["model"] == "nemotron_3_nano_omni"
    assert presets["nemotron"]["hasApiKey"] is False
    assert "apiKey" not in presets["nemotron"]


def test_nemotron_preset_enables_thinking_for_direct_calls():
    provider = llm_settings._apply_local_model_preset_extras(
        {"model": "nemotron_3_nano_omni", "chatTemplateKwargs": {}}
    )

    assert provider["chatTemplateKwargs"] == {"enable_thinking": True}


def test_connection_test_disables_thinking_and_limits_output(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def post(self, url, *, headers, json):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(llm_settings.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(
        llm_settings,
        "load_llm_settings",
        lambda include_secrets=True: {
            "providers": {
                "local": {
                    **llm_settings.LOCAL_NEMOTRON_PROVIDER,
                    "chatTemplateKwargs": {"enable_thinking": True},
                }
            }
        },
    )

    result = asyncio.run(
        llm_settings.test_llm_provider(llm_settings.LLMTestRequest(provider="local"))
    )

    assert result["ok"] is True
    assert captured["payload"]["max_tokens"] == 4
    assert captured["payload"]["chat_template_kwargs"] == {"enable_thinking": False}
