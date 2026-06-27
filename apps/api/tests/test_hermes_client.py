from services import hermes_client


def test_runs_url_does_not_fall_back_to_compat_port_by_default(monkeypatch):
    monkeypatch.delenv("SIQ_HERMES_ASSISTANT_RUNS_URL", raising=False)
    monkeypatch.delenv("HERMES_ASSISTANT_RUNS_URL", raising=False)
    monkeypatch.delenv("SIQ_HERMES_ALLOW_COMPAT_PORTS", raising=False)
    monkeypatch.setenv("SIQ_HERMES_ASSISTANT_PORT", "18642")
    monkeypatch.setattr(
        hermes_client,
        "_is_tcp_port_open",
        lambda host, port: port == 8642,
    )

    assert hermes_client._runs_url("siq_assistant", "ASSISTANT") == "http://127.0.0.1:18642/v1/runs"


def test_runs_url_can_use_compat_port_when_explicitly_allowed(monkeypatch):
    monkeypatch.delenv("SIQ_HERMES_ASSISTANT_RUNS_URL", raising=False)
    monkeypatch.delenv("HERMES_ASSISTANT_RUNS_URL", raising=False)
    monkeypatch.setenv("SIQ_HERMES_ASSISTANT_PORT", "18642")
    monkeypatch.setenv("SIQ_HERMES_ALLOW_COMPAT_PORTS", "1")
    monkeypatch.setattr(
        hermes_client,
        "_is_tcp_port_open",
        lambda host, port: port == 8642,
    )

    assert hermes_client._runs_url("siq_assistant", "ASSISTANT") == "http://127.0.0.1:8642/v1/runs"


def test_runs_url_keeps_explicit_runs_url_even_when_unhealthy(monkeypatch):
    monkeypatch.setenv("SIQ_HERMES_ASSISTANT_RUNS_URL", "http://127.0.0.1:9999/v1/runs/")
    monkeypatch.setattr(hermes_client, "_is_tcp_port_open", lambda host, port: True)

    assert hermes_client._runs_url("siq_assistant", "ASSISTANT") == "http://127.0.0.1:9999/v1/runs"


def test_profile_urls_detect_existing_compat_gateways_when_allowed(monkeypatch):
    monkeypatch.setenv("SIQ_HERMES_ASSISTANT_PORT", "18642")
    monkeypatch.setenv("SIQ_HERMES_ALLOW_COMPAT_PORTS", "1")
    monkeypatch.setattr(
        hermes_client,
        "_is_tcp_port_open",
        lambda host, port: port == 8642,
    )

    assert hermes_client.HERMES_PROFILES["siq_assistant"]["base"] == "http://127.0.0.1:8642/v1/runs"


def test_profile_config_is_resolved_dynamically(monkeypatch):
    monkeypatch.delenv("SIQ_HERMES_ALLOW_COMPAT_PORTS", raising=False)
    monkeypatch.setenv("SIQ_HERMES_ASSISTANT_PORT", "18642")
    monkeypatch.setattr(
        hermes_client,
        "_is_tcp_port_open",
        lambda host, port: port == 18642,
    )

    assert hermes_client.HERMES_PROFILES["siq_assistant"]["base"] == "http://127.0.0.1:18642/v1/runs"

    monkeypatch.setenv("SIQ_HERMES_ASSISTANT_PORT", "18643")
    assert hermes_client.HERMES_PROFILES["siq_assistant"]["base"] == "http://127.0.0.1:18643/v1/runs"


def test_profile_model_name_uses_siq_runtime_profile_when_present(tmp_path, monkeypatch):
    profiles_root = tmp_path / "profiles"
    profile = profiles_root / "siq_assistant"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    monkeypatch.setenv("SIQ_HERMES_PROFILES_ROOT", str(profiles_root))
    monkeypatch.delenv("SIQ_HERMES_ASSISTANT_MODEL", raising=False)

    assert hermes_client._profile_model_name("siq_assistant", "ASSISTANT") == "siq_assistant"


def test_profile_model_name_allows_explicit_model_override(tmp_path, monkeypatch):
    profiles_root = tmp_path / "profiles"
    profile = profiles_root / "siq_assistant"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    monkeypatch.setenv("SIQ_HERMES_PROFILES_ROOT", str(profiles_root))
    monkeypatch.setenv("SIQ_HERMES_ASSISTANT_MODEL", "siq_assistant")

    assert hermes_client._profile_model_name("siq_assistant", "ASSISTANT") == "siq_assistant"
