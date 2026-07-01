import json

import anyio
import httpx

from services import agent_chat_runtime as runtime


class _Request:
    def __init__(self, disconnected: bool = False):
        self.disconnected = disconnected

    async def is_disconnected(self) -> bool:
        return self.disconnected


async def _collect_events(profile: str, session_id: str, *, offset: int = 0) -> list[dict[str, str]]:
    events = []
    async for event in runtime.stream_active_run_events(
        _Request(),
        profile=profile,
        session_id=session_id,
        offset=offset,
    ):
        events.append(event)
    return events


def _event_payload(event: dict[str, str]) -> dict:
    return json.loads(event["data"])


def test_active_run_key_uses_canonical_profile_aliases():
    state = runtime.ActiveRunState(
        profile="siq_assistant",
        session_id="alias-key-session",
        run_id="run-alias-key",
    )
    key = runtime._active_key("assistant", state.session_id)
    runtime.ACTIVE_RUNS[key] = state
    try:
        assert key == ("siq_assistant", state.session_id)
        assert runtime.has_active_run("siq_assistant", state.session_id) is True
        assert runtime.has_active_run("assistant", state.session_id) is True
        snapshot = runtime.get_active_run_snapshot("assistant", state.session_id)
    finally:
        runtime.ACTIVE_RUNS.pop(key, None)

    assert snapshot["running"] is True
    assert snapshot["run_id"] == "run-alias-key"


def test_stream_active_run_events_replays_from_offset_and_drains_terminal_done():
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="offset-replay-session",
            run_id="run-offset-replay",
        )
        runtime.ACTIVE_RUNS[runtime._active_key(state.profile, state.session_id)] = state
        try:
            await runtime._append_state_event(state, "delta", {"content": "first"})
            await runtime._append_state_event(state, "progress", {"status": "running"})
            await runtime._append_state_event(state, "done", {"content": "final"})
            return await _collect_events(state.profile, state.session_id, offset=1), state
        finally:
            runtime.ACTIVE_RUNS.pop(runtime._active_key(state.profile, state.session_id), None)

    events, state = anyio.run(run_case)

    assert [event["event"] for event in events] == ["progress", "done"]
    assert state.status == "completed"
    assert state.done_payload == {"content": "final"}


def test_stream_active_run_events_drains_terminal_error_status():
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="terminal-error-session",
            run_id="run-terminal-error",
        )
        runtime.ACTIVE_RUNS[runtime._active_key(state.profile, state.session_id)] = state
        try:
            await runtime._append_state_event(state, "delta", {"content": "partial"})
            await runtime._append_state_event(state, "error", {"message": "Hermes failed"})
            return await _collect_events(state.profile, state.session_id, offset=0), state
        finally:
            runtime.ACTIVE_RUNS.pop(runtime._active_key(state.profile, state.session_id), None)

    events, state = anyio.run(run_case)

    assert [event["event"] for event in events] == ["delta", "error"]
    assert state.status == "failed"
    assert state.error == "Hermes failed"


def test_stream_active_run_events_returns_without_yielding_when_request_disconnects():
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="disconnect-session",
            run_id="run-disconnect",
        )
        runtime.ACTIVE_RUNS[runtime._active_key(state.profile, state.session_id)] = state
        try:
            await runtime._append_state_event(state, "delta", {"content": "already buffered"})
            events = []
            async for event in runtime.stream_active_run_events(
                _Request(disconnected=True),
                profile=state.profile,
                session_id=state.session_id,
            ):
                events.append(event)
            return events
        finally:
            runtime.ACTIVE_RUNS.pop(runtime._active_key(state.profile, state.session_id), None)

    assert anyio.run(run_case) == []


def test_stop_active_run_marks_user_stop_and_emits_replace(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="stop-replace-session",
            run_id="run-stop-replace",
        )
        runtime.ACTIVE_RUNS[runtime._active_key(state.profile, state.session_id)] = state

        async def fake_stop_run(run_id, *, profile):
            return {"id": run_id, "status": "stopped", "profile": profile}

        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        try:
            result = await runtime.stop_active_run(state.profile, state.session_id)
            return result, state
        finally:
            runtime.ACTIVE_RUNS.pop(runtime._active_key(state.profile, state.session_id), None)

    result, state = anyio.run(run_case)

    assert result == {
        "stopped": True,
        "run_id": "run-stop-replace",
        "hermes": {"id": "run-stop-replace", "status": "stopped", "profile": "siq_assistant"},
    }
    assert state.stop_requested is True
    assert state.user_stop_requested is True
    assert [event["event"] for event in state.events] == ["progress", "replace"]
    assert _event_payload(state.events[-1]) == {"content": runtime.STOPPED_MESSAGE}


def test_stop_active_run_404_orphan_emits_error_and_cleans_active_run(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="orphan-cleanup-session",
            run_id="run-orphan-cleanup",
        )
        key = runtime._active_key(state.profile, state.session_id)
        runtime.ACTIVE_RUNS[key] = state

        async def fake_stop_run(_run_id, *, profile):
            request = httpx.Request("POST", f"https://hermes.example/runs/{_run_id}/stop")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)

        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        result = await runtime.stop_active_run(state.profile, state.session_id)
        return result, state, key in runtime.ACTIVE_RUNS

    result, state, still_active = anyio.run(run_case)

    assert result == {
        "stopped": True,
        "run_id": "run-orphan-cleanup",
        "detail": runtime.ORPHANED_RUN_MESSAGE,
        "hermes": {"error": "run_not_found"},
    }
    assert still_active is False
    assert [event["event"] for event in state.events] == ["progress", "replace", "progress", "replace", "error"]
    assert _event_payload(state.events[-1]) == {
        "message": runtime.ORPHANED_RUN_MESSAGE,
        "reason": "hermes_run_not_found",
    }


def test_stop_active_run_without_state_returns_not_stopped():
    result = anyio.run(runtime.stop_active_run, "siq_assistant", "missing-active-run")

    assert result == {"stopped": False, "detail": "No active Hermes run"}
