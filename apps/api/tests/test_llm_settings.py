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
    legacy_config = tmp_path / "apps" / "api" / ".finsight" / "llm_settings.json"
    new_config.parent.mkdir(parents=True)
    legacy_config.parent.mkdir(parents=True)
    new_config.write_text("{}", encoding="utf-8")
    legacy_config.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(llm_settings, "CONFIG_PATH", new_config)
    monkeypatch.setattr(llm_settings, "LEGACY_CONFIG_PATHS", (legacy_config,))

    assert llm_settings._settings_source_path() == new_config
