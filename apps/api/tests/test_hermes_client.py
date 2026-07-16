import anyio

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


def test_profile_auth_header_prefers_profile_specific_key(monkeypatch):
    monkeypatch.setenv("HERMES_API_KEY", "global-key")
    monkeypatch.setenv("SIQ_HERMES_ANALYSIS_API_KEY", "analysis-key")

    assert hermes_client._hermes_auth_header("analysis") == "Bearer analysis-key"
    assert hermes_client._hermes_auth_header("siq_assistant") == "Bearer global-key"


def test_profile_auth_header_keeps_existing_bearer_prefix(monkeypatch):
    monkeypatch.delenv("HERMES_API_KEY", raising=False)
    monkeypatch.delenv("HERMES_TOKEN", raising=False)
    monkeypatch.setenv("HERMES_ANALYSIS_TOKEN", "Bearer analysis-token")

    assert hermes_client._hermes_auth_header("siq_analysis") == "Bearer analysis-token"


def test_profile_auth_header_fails_closed_without_a_key(monkeypatch):
    for name in (
        "SIQ_HERMES_ANALYSIS_API_KEY",
        "HERMES_ANALYSIS_API_KEY",
        "SIQ_HERMES_ANALYSIS_TOKEN",
        "HERMES_ANALYSIS_TOKEN",
        "HERMES_API_KEY",
        "HERMES_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)

    try:
        hermes_client._hermes_auth_header("siq_analysis")
    except RuntimeError as exc:
        assert str(exc) == "Hermes API key is not configured for profile siq_analysis."
    else:
        raise AssertionError("missing profile key must fail closed")


def test_terminal_accumulator_keeps_first_terminal_and_received_text():
    accumulator = hermes_client.RunTerminalAccumulator("run-terminal-contract")

    accumulator.accept(hermes_client.StreamEvent(type="delta", text="partial "))
    first = accumulator.accept(
        hermes_client.StreamEvent(type="failed", text="upstream failed", status="failed", error=True)
    )
    duplicate = accumulator.accept(
        hermes_client.StreamEvent(type="done", text="must not replace failure", status="completed")
    )

    assert first is not None
    assert first.schema_version == "siq.hermes.run_terminal.v1"
    assert first.status == "failed"
    assert first.error_code == "hermes_run_failed"
    assert first.retryable is True
    assert first.received_text == "partial "
    assert first.diagnostic == "upstream failed"
    assert duplicate == first


def test_runtime_metadata_is_strictly_projected_without_extra_fields():
    runtime = hermes_client.normalize_run_runtime(
        {
            "schema_version": "hermes.run_runtime.v1",
            "requested_model": "siq_ic_chairman",
            "configured": {
                "provider": "minimax-cn",
                "model": "MiniMax-M3",
                "api_key": "configured-secret",
            },
            "effective": {
                "provider": "custom:stepfun-step-3.7-flash",
                "model": "step-3.7-flash",
                "base_url": "https://secret.example/v1",
            },
            "fallback": {"activated": True, "reason": "secret detail"},
            "prompt": "secret prompt",
        }
    )

    assert runtime is not None
    assert runtime.to_payload() == {
        "schema_version": "hermes.run_runtime.v1",
        "requested_model": "siq_ic_chairman",
        "configured": {"provider": "minimax-cn", "model": "MiniMax-M3"},
        "effective": {
            "provider": "custom:stepfun-step-3.7-flash",
            "model": "step-3.7-flash",
        },
        "fallback": {"activated": True},
    }
    assert "secret" not in str(runtime.to_payload()).lower()


def test_runtime_metadata_rejects_malformed_or_unsafe_known_fields():
    valid = {
        "schema_version": "hermes.run_runtime.v1",
        "requested_model": "siq_ic_chairman",
        "configured": {"provider": "minimax-cn", "model": "MiniMax-M3"},
        "effective": {"provider": "minimax-cn", "model": "MiniMax-M3"},
        "fallback": {"activated": False},
    }

    assert hermes_client.normalize_run_runtime({**valid, "schema_version": "unknown"}) is None
    assert (
        hermes_client.normalize_run_runtime(
            {**valid, "effective": {"provider": "https://secret.example", "model": "MiniMax-M3"}}
        )
        is None
    )
    assert hermes_client.normalize_run_runtime({**valid, "fallback": {"activated": "false"}}) is None


def test_terminal_result_carries_runtime_for_success_and_failure():
    runtime = hermes_client.normalize_run_runtime(
        {
            "schema_version": "hermes.run_runtime.v1",
            "requested_model": "siq_ic_chairman",
            "configured": {"provider": "minimax-cn", "model": "MiniMax-M3"},
            "effective": {"provider": "minimax-cn", "model": "MiniMax-M3"},
            "fallback": {"activated": False},
        }
    )
    assert runtime is not None

    succeeded = hermes_client.RunTerminalAccumulator("run-success").accept(
        hermes_client.StreamEvent(type="done", text="{}", status="completed", runtime=runtime)
    )
    failed = hermes_client.RunTerminalAccumulator("run-failed").accept(
        hermes_client.StreamEvent(type="failed", text="abort", status="failed", runtime=runtime)
    )

    assert succeeded is not None and succeeded.runtime == runtime
    assert failed is not None and failed.runtime == runtime
    assert succeeded.to_payload()["runtime"] == runtime.to_payload()
    assert failed.to_payload()["runtime"] == runtime.to_payload()


def test_collect_run_terminal_result_projects_success_and_eof(monkeypatch):
    async def run_case():
        async def completed_stream(*_args, **_kwargs):
            yield hermes_client.StreamEvent(type="delta", text="hello")
            yield hermes_client.StreamEvent(type="done", text="hello world", status="completed")

        monkeypatch.setattr(hermes_client, "stream_run", completed_stream)
        completed = await hermes_client.collect_run_terminal_result("run-completed")

        async def eof_stream(*_args, **_kwargs):
            yield hermes_client.StreamEvent(type="delta", text="partial")

        monkeypatch.setattr(hermes_client, "stream_run", eof_stream)
        eof = await hermes_client.collect_run_terminal_result("run-eof")
        return completed, eof

    completed, eof = anyio.run(run_case)

    assert completed.status == "succeeded"
    assert completed.received_text == "hello world"
    assert completed.error_code is None
    assert eof.status == "protocol_eof"
    assert eof.error_code == "hermes_protocol_eof"
    assert eof.retryable is True
    assert eof.received_text == "partial"
    assert hermes_client.pop_run_terminal_result("run-completed") == completed
    assert hermes_client.pop_run_terminal_result("run-eof") == eof
    assert hermes_client.pop_run_terminal_result("run-eof") is None


def test_terminal_cache_is_bounded_and_can_be_discarded(monkeypatch):
    monkeypatch.setattr(hermes_client, "_RECENT_RUN_TERMINAL_LIMIT", 2)
    hermes_client._RECENT_RUN_TERMINALS.clear()
    for index in range(3):
        hermes_client._remember_run_terminal(
            hermes_client.RunTerminalResult(run_id=f"run-{index}", status="succeeded")
        )

    assert list(hermes_client._RECENT_RUN_TERMINALS) == ["run-1", "run-2"]
    hermes_client.discard_run_terminal_result("run-1")
    assert list(hermes_client._RECENT_RUN_TERMINALS) == ["run-2"]


def test_collect_run_result_raises_structured_error_for_failed_partial(monkeypatch):
    async def run_case():
        async def failed_stream(*_args, **_kwargs):
            yield hermes_client.StreamEvent(type="delta", text="partial")
            yield hermes_client.StreamEvent(type="failed", text="gateway detail", status="failed", error=True)

        monkeypatch.setattr(hermes_client, "stream_run", failed_stream)
        try:
            await hermes_client.collect_run_result("run-failed")
        except hermes_client.RunTerminalError as exc:
            return exc.result
        raise AssertionError("failed run must raise RunTerminalError")

    result = anyio.run(run_case)

    assert result.status == "failed"
    assert result.received_text == "partial"
    assert result.diagnostic == "gateway detail"
