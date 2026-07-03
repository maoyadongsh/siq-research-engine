from services import hermes_client


def test_ic_profile_aliases_normalize_to_siq_canonical_ids():
    assert hermes_client.normalize_profile("ic_master") == "siq_ic_master_coordinator"
    assert hermes_client.normalize_profile("ic_coordinator") == "siq_ic_master_coordinator"
    assert hermes_client.normalize_profile("ic_chairman") == "siq_ic_chairman"
    assert hermes_client.normalize_profile("ic_strategy") == "siq_ic_strategist"
    assert hermes_client.normalize_profile("ic_strategist") == "siq_ic_strategist"
    assert hermes_client.normalize_profile("ic_sector") == "siq_ic_sector_expert"
    assert hermes_client.normalize_profile("ic_finance") == "siq_ic_finance_auditor"
    assert hermes_client.normalize_profile("ic_legal") == "siq_ic_legal_scanner"
    assert hermes_client.normalize_profile("ic_risk") == "siq_ic_risk_controller"


def test_ic_profiles_have_default_and_compat_ports():
    expected_defaults = {
        "siq_ic_master_coordinator": 18660,
        "siq_ic_chairman": 18661,
        "siq_ic_strategist": 18662,
        "siq_ic_sector_expert": 18663,
        "siq_ic_finance_auditor": 18664,
        "siq_ic_legal_scanner": 18665,
        "siq_ic_risk_controller": 18666,
    }
    expected_compat = {
        "siq_ic_master_coordinator": 8660,
        "siq_ic_chairman": 8661,
        "siq_ic_strategist": 8662,
        "siq_ic_sector_expert": 8663,
        "siq_ic_finance_auditor": 8664,
        "siq_ic_legal_scanner": 8665,
        "siq_ic_risk_controller": 8666,
    }

    for profile, port in expected_defaults.items():
        assert hermes_client.SIQ_HERMES_DEFAULT_PORTS[profile] == port
        assert hermes_client.HERMES_COMPAT_PORTS[profile] == expected_compat[profile]


def test_ic_runs_url_uses_default_port(monkeypatch):
    monkeypatch.delenv("SIQ_HERMES_IC_MASTER_RUNS_URL", raising=False)
    monkeypatch.delenv("HERMES_IC_MASTER_RUNS_URL", raising=False)
    monkeypatch.delenv("SIQ_HERMES_IC_MASTER_PORT", raising=False)
    monkeypatch.delenv("HERMES_IC_MASTER_PORT", raising=False)
    monkeypatch.delenv("SIQ_HERMES_ALLOW_COMPAT_PORTS", raising=False)
    monkeypatch.setattr(hermes_client, "_is_tcp_port_open", lambda host, port: False)

    assert (
        hermes_client._runs_url("siq_ic_master_coordinator", "IC_MASTER")
        == "http://127.0.0.1:18660/v1/runs"
    )


def test_ic_runs_url_allows_env_port_override(monkeypatch):
    monkeypatch.delenv("SIQ_HERMES_IC_RISK_RUNS_URL", raising=False)
    monkeypatch.delenv("HERMES_IC_RISK_RUNS_URL", raising=False)
    monkeypatch.setenv("SIQ_HERMES_IC_RISK_PORT", "19666")
    monkeypatch.setattr(hermes_client, "_is_tcp_port_open", lambda host, port: port == 19666)

    assert (
        hermes_client._runs_url("siq_ic_risk_controller", "IC_RISK")
        == "http://127.0.0.1:19666/v1/runs"
    )


def test_ic_profile_model_name_uses_runtime_profile_dir(tmp_path, monkeypatch):
    profiles_root = tmp_path / "profiles"
    profile = profiles_root / "siq_ic_legal_scanner"
    profile.mkdir(parents=True)
    (profile / "config.yaml").write_text("model: {}\n", encoding="utf-8")
    monkeypatch.setenv("SIQ_HERMES_PROFILES_ROOT", str(profiles_root))
    monkeypatch.delenv("SIQ_HERMES_IC_LEGAL_MODEL", raising=False)

    assert hermes_client._profile_model_name("siq_ic_legal_scanner", "IC_LEGAL") == "siq_ic_legal_scanner"
