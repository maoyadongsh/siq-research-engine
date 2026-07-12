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
