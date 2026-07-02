import asyncio
import json

import anyio
import httpx

from services import agent_chat_runtime as runtime
from services import agent_runtime_sessions, agent_runtime_streaming
from services.hermes_client import StreamEvent


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


async def _collect_chat_stream_events(
    profile: str,
    session_id: str,
) -> list[dict[str, str]]:
    events = []
    async for event in runtime.stream_chat_reply(
        "join active run",
        _Request(),
        object(),
        profile=profile,
        session_id=session_id,
        history_limit=1,
    ):
        events.append(event)
    return events


async def _collect_active_events_limit(profile: str, session_id: str, limit: int) -> list[dict[str, str]]:
    events = []
    stream = runtime.stream_active_run_events(
        _Request(),
        profile=profile,
        session_id=session_id,
    )
    try:
        async for event in stream:
            events.append(event)
            if len(events) >= limit:
                break
    finally:
        await stream.aclose()
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


def test_streaming_owner_state_is_shared_by_public_facades():
    assert runtime.ACTIVE_RUNS is agent_runtime_streaming.ACTIVE_RUNS
    assert agent_runtime_sessions.ACTIVE_RUNS is agent_runtime_streaming.ACTIVE_RUNS
    assert runtime.ActiveRunState is agent_runtime_streaming.ActiveRunState
    assert runtime._append_state_event is agent_runtime_streaming._append_state_event
    assert runtime._append_completed_active_run is agent_runtime_streaming._append_completed_active_run
    assert runtime._append_reasoning_active_run is agent_runtime_streaming._append_reasoning_active_run
    assert runtime._append_user_stopped_active_run is agent_runtime_streaming._append_user_stopped_active_run
    assert runtime._clear_active_run is agent_runtime_streaming._clear_active_run
    assert runtime._active_key is agent_runtime_streaming._active_key

    state = agent_runtime_streaming.ActiveRunState(
        profile="siq_assistant",
        session_id="streaming-owner-shared-session",
        run_id="run-streaming-owner-shared",
    )
    key = agent_runtime_streaming._active_key("assistant", state.session_id)
    agent_runtime_streaming.ACTIVE_RUNS[key] = state
    try:
        assert runtime.has_active_run("siq_assistant", state.session_id) is True
        snapshot = runtime.get_active_run_snapshot("assistant", state.session_id)
    finally:
        agent_runtime_streaming.ACTIVE_RUNS.pop(key, None)

    assert snapshot["running"] is True
    assert snapshot["run_id"] == "run-streaming-owner-shared"


def test_streaming_owner_appends_terminal_events_and_clears_active_run():
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="streaming-terminal-owner-session",
            run_id="run-streaming-terminal-owner",
        )
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        await runtime._append_completed_active_run(state, {"content": "final", "new_achievements": []})
        completed_snapshot = runtime.get_active_run_snapshot("assistant", state.session_id)
        runtime._clear_active_run(state)
        after_clear = runtime.get_active_run_snapshot("siq_assistant", state.session_id)
        return state, completed_snapshot, after_clear

    state, completed_snapshot, after_clear = anyio.run(run_case)

    assert [event["event"] for event in state.events] == ["progress", "done"]
    assert _event_payload(state.events[0])["status"] == "completed"
    assert _event_payload(state.events[1]) == {"content": "final", "new_achievements": []}
    assert state.status == "completed"
    assert completed_snapshot["running"] is False
    assert completed_snapshot["status"] == "completed"
    assert after_clear == {"running": False}


def test_streaming_owner_appends_user_stopped_terminal_error():
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="streaming-user-stopped-owner-session",
            run_id="run-streaming-user-stopped-owner",
        )
        await runtime._append_user_stopped_active_run(state, runtime.STOPPED_MESSAGE)
        return state

    state = anyio.run(run_case)

    assert [event["event"] for event in state.events] == ["progress", "error"]
    assert state.status == "failed"
    assert state.error == runtime.STOPPED_MESSAGE
    assert _event_payload(state.events[0])["status"] == "stopped"
    assert _event_payload(state.events[1]) == {
        "message": runtime.STOPPED_MESSAGE,
        "reason": "user_stop_requested",
    }


def test_streaming_owner_appends_reasoning_event_and_progress():
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="streaming-reasoning-owner-session",
            run_id="run-streaming-reasoning-owner",
        )
        await runtime._append_reasoning_active_run(state, "reasoning detail")
        return state

    state = anyio.run(run_case)

    assert [event["event"] for event in state.events] == ["reasoning", "progress"]
    assert _event_payload(state.events[0]) == {"text": "reasoning detail"}
    assert _event_payload(state.events[1])["status"] == "running"
    assert _event_payload(state.events[1])["title"] == "正在推理"
    assert _event_payload(state.events[1])["detail"] == "reasoning detail"
    assert _event_payload(state.events[1])["source"] == "reasoning"
    assert state.status == "running"
    assert state.content == ""


def test_collect_stream_run_success_uses_streaming_terminal_owner(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="collect-success-session",
            run_id="run-collect-success",
        )
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        saved_messages = []

        async def fake_stream_run(run_id, *, profile, timeout):
            assert (run_id, profile) == ("run-collect-success", "siq_assistant")
            assert timeout == 1
            yield StreamEvent(type="delta", text="hello")
            yield StreamEvent(type="done", text="hello")

        async def fake_save_message(role, content, session_id, *, profile=None):
            saved_messages.append((role, content, session_id, profile))

        async def fake_done_payload(reply):
            return {"new_achievements": ["ok"], "reply_length": len(reply)}

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: 1)
        monkeypatch.setattr(runtime, "stream_idle_timeout", lambda _profile: 1)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message)
        await runtime._append_state_event(state, "run", {"run_id": state.run_id, "session_id": state.session_id})
        await runtime._collect_stream_run(state, fake_done_payload)
        return state, saved_messages, key in runtime.ACTIVE_RUNS

    state, saved_messages, still_active = anyio.run(run_case)

    assert still_active is False
    assert [event["event"] for event in state.events] == ["run", "progress", "delta", "progress", "done"]
    assert _event_payload(state.events[1])["status"] == "running"
    assert _event_payload(state.events[3])["status"] == "completed"
    assert _event_payload(state.events[4]) == {
        "new_achievements": ["ok"],
        "reply_length": 5,
        "content": "hello",
    }
    assert saved_messages == [("assistant", "hello", "collect-success-session", "siq_assistant")]
    assert state.status == "completed"


def test_collect_stream_run_reasoning_uses_streaming_event_owner(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="collect-reasoning-session",
            run_id="run-collect-reasoning",
        )
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        saved_messages = []

        async def fake_stream_run(run_id, *, profile, timeout):
            assert (run_id, profile) == ("run-collect-reasoning", "siq_assistant")
            assert timeout == 1
            yield StreamEvent(type="reasoning", text="thinking through evidence")
            yield StreamEvent(type="delta", text="answer")
            yield StreamEvent(type="done", text="answer")

        async def fake_save_message(role, content, session_id, *, profile=None):
            saved_messages.append((role, content, session_id, profile))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: 1)
        monkeypatch.setattr(runtime, "stream_idle_timeout", lambda _profile: 1)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message)
        await runtime._collect_stream_run(state, None)
        return state, saved_messages, key in runtime.ACTIVE_RUNS

    state, saved_messages, still_active = anyio.run(run_case)

    assert still_active is False
    assert [event["event"] for event in state.events] == [
        "progress",
        "reasoning",
        "progress",
        "delta",
        "progress",
        "done",
    ]
    assert _event_payload(state.events[1]) == {"text": "thinking through evidence"}
    assert _event_payload(state.events[2])["source"] == "reasoning"
    assert _event_payload(state.events[2])["detail"] == "thinking through evidence"
    assert _event_payload(state.events[3]) == {"content": "answer"}
    assert _event_payload(state.events[-1]) == {"new_achievements": [], "content": "answer"}
    assert saved_messages == [("assistant", "answer", "collect-reasoning-session", "siq_assistant")]
    assert state.status == "completed"


def test_collect_stream_run_user_stop_uses_streaming_terminal_owner(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="collect-user-stop-session",
            run_id="run-collect-user-stop",
        )
        state.user_stop_requested = True
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        saved_messages = []

        async def fake_stream_run(run_id, *, profile, timeout):
            assert (run_id, profile) == ("run-collect-user-stop", "siq_assistant")
            yield StreamEvent(type="cancelled", text="")

        async def fake_save_message(role, content, session_id, *, profile=None):
            saved_messages.append((role, content, session_id, profile))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: 1)
        monkeypatch.setattr(runtime, "stream_idle_timeout", lambda _profile: 1)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message)
        await runtime._collect_stream_run(state, None)
        return state, saved_messages, key in runtime.ACTIVE_RUNS

    state, saved_messages, still_active = anyio.run(run_case)

    assert still_active is False
    assert "done" not in [event["event"] for event in state.events]
    assert any(event["event"] == "replace" and _event_payload(event) == {"content": runtime.STOPPED_MESSAGE} for event in state.events)
    assert _event_payload(state.events[-1]) == {
        "message": runtime.STOPPED_MESSAGE,
        "reason": "user_stop_requested",
    }
    assert saved_messages == [("assistant", runtime.STOPPED_MESSAGE, "collect-user-stop-session", "siq_assistant")]
    assert state.status == "failed"


def test_collect_stream_run_cancelled_without_user_stop_saves_failed_history(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="collect-cancelled-session",
            run_id="run-collect-cancelled",
        )
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        saved_messages = []

        async def fake_stream_run(run_id, *, profile, timeout):
            assert (run_id, profile) == ("run-collect-cancelled", "siq_assistant")
            yield StreamEvent(type="cancelled", text="cancel detail")

        async def fake_save_message(role, content, session_id, *, profile=None):
            saved_messages.append((role, content, session_id, profile))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: 1)
        monkeypatch.setattr(runtime, "stream_idle_timeout", lambda _profile: 1)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message)
        await runtime._collect_stream_run(state, None)
        return state, saved_messages, key in runtime.ACTIVE_RUNS

    state, saved_messages, still_active = anyio.run(run_case)

    assert still_active is False
    assert "done" not in [event["event"] for event in state.events]
    assert runtime.RUN_CANCELLED_MESSAGE in _event_payload(state.events[1])["content"]
    assert _event_payload(state.events[-1])["reason"] == "run_cancelled"
    assert saved_messages == [
        (
            "assistant",
            f"{runtime.RUN_CANCELLED_MESSAGE}\n\ncancel detail",
            "collect-cancelled-session",
            "siq_assistant",
        )
    ]
    assert state.status == "failed"


def test_collect_stream_run_idle_timeout_stops_run_and_clears_active(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="collect-idle-timeout-session",
            run_id="run-collect-idle-timeout",
        )
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        saved_messages = []
        stop_calls = []

        async def fake_stream_run(run_id, *, profile, timeout):
            assert (run_id, profile) == ("run-collect-idle-timeout", "siq_assistant")
            raise asyncio.TimeoutError()
            yield StreamEvent(type="delta", text="unreachable")

        async def fake_stop_run(run_id, *, profile):
            stop_calls.append((run_id, profile))
            return {"id": run_id, "status": "stopped"}

        async def fake_save_message(role, content, session_id, *, profile=None):
            saved_messages.append((role, content, session_id, profile))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: 1)
        monkeypatch.setattr(runtime, "stream_idle_timeout", lambda _profile: 0.001)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message)
        with anyio.fail_after(1):
            await runtime._collect_stream_run(state, None)
        return state, saved_messages, stop_calls, key in runtime.ACTIVE_RUNS

    state, saved_messages, stop_calls, still_active = anyio.run(run_case)

    assert still_active is False
    assert stop_calls == [("run-collect-idle-timeout", "siq_assistant")]
    assert [event["event"] for event in state.events] == ["progress", "progress", "delta", "progress", "done"]
    assert _event_payload(state.events[1])["status"] == "error"
    assert runtime.IDLE_TIMEOUT_MESSAGE in _event_payload(state.events[2])["content"]
    assert _event_payload(state.events[-1])["content"] == runtime.IDLE_TIMEOUT_MESSAGE
    assert saved_messages == [("assistant", runtime.IDLE_TIMEOUT_MESSAGE, "collect-idle-timeout-session", "siq_assistant")]
    assert state.status == "completed"


def test_collect_stream_run_http_timeout_stops_run_and_clears_active(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="collect-http-timeout-session",
            run_id="run-collect-http-timeout",
        )
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        saved_messages = []
        stop_calls = []

        async def fake_stream_run(run_id, *, profile, timeout):
            assert (run_id, profile) == ("run-collect-http-timeout", "siq_assistant")
            raise httpx.TimeoutException("stream timed out")
            yield StreamEvent(type="delta", text="unreachable")

        async def fake_stop_run(run_id, *, profile):
            stop_calls.append((run_id, profile))
            return {"id": run_id, "status": "stopped"}

        async def fake_save_message(role, content, session_id, *, profile=None):
            saved_messages.append((role, content, session_id, profile))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: 1)
        monkeypatch.setattr(runtime, "stream_idle_timeout", lambda _profile: 1)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message)
        await runtime._collect_stream_run(state, None)
        return state, saved_messages, stop_calls, key in runtime.ACTIVE_RUNS

    state, saved_messages, stop_calls, still_active = anyio.run(run_case)

    assert still_active is False
    assert stop_calls == [("run-collect-http-timeout", "siq_assistant")]
    assert [event["event"] for event in state.events] == ["progress", "progress", "delta", "progress", "done"]
    assert _event_payload(state.events[1])["status"] == "error"
    assert runtime.TIMEOUT_MESSAGE in _event_payload(state.events[2])["content"]
    assert _event_payload(state.events[-1])["content"] == runtime.TIMEOUT_MESSAGE
    assert saved_messages == [("assistant", runtime.TIMEOUT_MESSAGE, "collect-http-timeout-session", "siq_assistant")]
    assert state.status == "completed"


def test_collect_stream_run_repeated_tool_call_stops_run(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="collect-repeated-tool-session",
            run_id="run-collect-repeated-tool",
        )
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        saved_messages = []
        stop_calls = []

        async def fake_stream_run(run_id, *, profile, timeout):
            assert (run_id, profile) == ("run-collect-repeated-tool", "siq_assistant")
            for _index in range(runtime.REPEATED_TOOL_CALL_LIMIT):
                yield StreamEvent(type="tool.started", tool="terminal", preview="python loop.py")

        async def fake_stop_run(run_id, *, profile):
            stop_calls.append((run_id, profile))
            return {"id": run_id, "status": "stopped"}

        async def fake_save_message(role, content, session_id, *, profile=None):
            saved_messages.append((role, content, session_id, profile))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: 1)
        monkeypatch.setattr(runtime, "stream_idle_timeout", lambda _profile: 1)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message)
        await runtime._collect_stream_run(state, None)
        return state, saved_messages, stop_calls, key in runtime.ACTIVE_RUNS

    state, saved_messages, stop_calls, still_active = anyio.run(run_case)

    assert still_active is False
    assert stop_calls == [("run-collect-repeated-tool", "siq_assistant")]
    assert "done" not in [event["event"] for event in state.events]
    assert _event_payload(state.events[-1])["reason"] == "repeated_tool_calls_without_delta"
    assert saved_messages == [("assistant", runtime.OUTPUT_LOOP_STOP_MESSAGE, "collect-repeated-tool-session", "siq_assistant")]
    assert state.status == "failed"


def test_collect_stream_run_consecutive_tool_errors_stop_run(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="collect-tool-errors-session",
            run_id="run-collect-tool-errors",
        )
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        saved_messages = []
        stop_calls = []

        async def fake_stream_run(run_id, *, profile, timeout):
            assert (run_id, profile) == ("run-collect-tool-errors", "siq_assistant")
            yield StreamEvent(type="tool.started", tool="terminal", preview="missing-file")
            for _index in range(runtime.CONSECUTIVE_TOOL_ERROR_LIMIT):
                yield StreamEvent(type="tool.completed", tool="terminal", duration=0.1, error=True)

        async def fake_stop_run(run_id, *, profile):
            stop_calls.append((run_id, profile))
            return {"id": run_id, "status": "stopped"}

        async def fake_save_message(role, content, session_id, *, profile=None):
            saved_messages.append((role, content, session_id, profile))

        monkeypatch.setattr(runtime, "stream_run", fake_stream_run)
        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        monkeypatch.setattr(runtime, "hermes_timeout", lambda: 1)
        monkeypatch.setattr(runtime, "stream_idle_timeout", lambda _profile: 1)
        monkeypatch.setattr(runtime, "save_message_in_background", fake_save_message)
        await runtime._collect_stream_run(state, None)
        return state, saved_messages, stop_calls, key in runtime.ACTIVE_RUNS

    state, saved_messages, stop_calls, still_active = anyio.run(run_case)

    assert still_active is False
    assert stop_calls == [("run-collect-tool-errors", "siq_assistant")]
    assert "done" not in [event["event"] for event in state.events]
    assert _event_payload(state.events[-1])["reason"] == "consecutive_tool_errors"
    assert saved_messages == [("assistant", runtime.OUTPUT_LOOP_STOP_MESSAGE, "collect-tool-errors-session", "siq_assistant")]
    assert state.status == "failed"


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


def test_stream_active_run_events_emits_heartbeat_without_buffering(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="heartbeat-session",
            run_id="run-heartbeat",
        )
        key = runtime._active_key(state.profile, state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        monkeypatch.setattr(runtime, "STREAM_EVENT_HEARTBEAT_SECONDS", 0.01)

        stream = runtime.stream_active_run_events(
            _Request(),
            profile="assistant",
            session_id=state.session_id,
        )
        try:
            event = await stream.__anext__()
            snapshot = runtime.get_active_run_snapshot("siq_assistant", state.session_id)
            return event, snapshot, list(state.events)
        finally:
            await stream.aclose()
            runtime.ACTIVE_RUNS.pop(key, None)

    event, snapshot, buffered_events = anyio.run(run_case)

    assert event["event"] == "progress"
    payload = _event_payload(event)
    assert payload["status"] == "running"
    assert payload["title"] == "等待模型或工具返回"
    assert payload["detail"] == "后台 Hermes run 仍在运行；本地模型可能正在生成首轮输出，或工具正在执行。"
    assert payload["source"] == "runtime"
    assert payload["updated_at"]
    assert buffered_events == []
    assert snapshot["event_count"] == 0
    assert snapshot["progress"] is None


def test_stream_chat_reply_joins_existing_active_run_snapshot(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="join-existing-session",
            run_id="run-join-existing",
        )
        key = runtime._active_key(state.profile, state.session_id)
        runtime.ACTIVE_RUNS[key] = state

        async def fail_create_run(*_args, **_kwargs):
            raise AssertionError("join path must not create a second Hermes run")

        monkeypatch.setattr(runtime, "create_run", fail_create_run)
        try:
            await runtime._append_state_event(state, "run", {"run_id": state.run_id, "session_id": state.session_id})
            await runtime._append_state_event(state, "delta", {"content": "already running"})
            before = runtime.get_active_run_snapshot("assistant", state.session_id)

            async def finish_run():
                await anyio.sleep(0)
                await runtime._append_state_event(state, "done", {"content": "already running"})

            async with anyio.create_task_group() as tg:
                tg.start_soon(finish_run)
                events = await _collect_chat_stream_events("assistant", state.session_id)
            after = runtime.get_active_run_snapshot("siq_assistant", state.session_id)
            return events, before, after
        finally:
            runtime.ACTIVE_RUNS.pop(key, None)

    events, before, after = anyio.run(run_case)

    assert [event["event"] for event in events] == ["run", "delta", "done"]
    assert _event_payload(events[0]) == {
        "run_id": "run-join-existing",
        "session_id": "join-existing-session",
    }
    assert before["running"] is True
    assert before["content"] == "already running"
    assert before["event_count"] == 2
    assert after["running"] is False
    assert after["status"] == "completed"
    assert after["content"] == "already running"


def test_session_default_contexts_are_isolated_by_profile_and_session(monkeypatch):
    touched_keys = [
        runtime._active_key("assistant", "default-context-a"),
        runtime._active_key("assistant", "default-context-b"),
        runtime._active_key("siq_analysis", "default-context-a"),
    ]
    for key in touched_keys:
        runtime.SESSION_DEFAULT_CONTEXTS.pop(key, None)

    def fake_format_context(context):
        return f"ctx:{context['company']}" if context else None

    monkeypatch.setattr(runtime, "format_chat_context", fake_format_context)
    try:
        assistant_a = runtime.get_session_default_context(
            "assistant",
            "default-context-a",
            {"company": "A"},
            allow_initialize=True,
        )
        assistant_b = runtime.get_session_default_context(
            "siq_assistant",
            "default-context-b",
            {"company": "B"},
            allow_initialize=True,
        )
        assistant_a_again = runtime.get_session_default_context(
            "siq_assistant",
            "default-context-a",
            {"company": "ignored"},
            allow_initialize=True,
        )
        analysis_a = runtime.get_session_default_context(
            "siq_analysis",
            "default-context-a",
            {"company": "analysis"},
            allow_initialize=True,
        )
    finally:
        for key in touched_keys:
            runtime.SESSION_DEFAULT_CONTEXTS.pop(key, None)

    assert assistant_a == "ctx:A"
    assert assistant_b == "ctx:B"
    assert assistant_a_again == "ctx:A"
    assert analysis_a == "ctx:analysis"


def test_stop_by_profile_alias_can_be_streamed_from_canonical_profile(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="alias-stop-stream-session",
            run_id="run-alias-stop-stream",
        )
        key = runtime._active_key(state.profile, state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        stop_calls = []

        async def fake_stop_run(run_id, *, profile):
            stop_calls.append((run_id, profile))
            return {"id": run_id, "status": "stopped", "profile": profile}

        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        try:
            result = await runtime.stop_active_run("assistant", state.session_id)
            events = await _collect_active_events_limit("siq_assistant", state.session_id, 2)
            return result, events, state, stop_calls
        finally:
            runtime.ACTIVE_RUNS.pop(key, None)

    result, events, state, stop_calls = anyio.run(run_case)

    assert result["stopped"] is True
    assert result["run_id"] == "run-alias-stop-stream"
    assert stop_calls == [("run-alias-stop-stream", "siq_assistant")]
    assert state.user_stop_requested is True
    assert [event["event"] for event in events] == ["progress", "replace"]
    assert _event_payload(events[-1]) == {"content": runtime.STOPPED_MESSAGE}


def test_terminal_snapshot_is_stable_after_stream_drain():
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="terminal-snapshot-session",
            run_id="run-terminal-snapshot",
        )
        key = runtime._active_key(state.profile, state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        try:
            await runtime._append_state_event(state, "delta", {"content": "final answer"})
            await runtime._append_state_event(state, "done", {"content": "final answer", "new_achievements": []})
            before = runtime.get_active_run_snapshot("assistant", state.session_id)
            events = await _collect_events("siq_assistant", state.session_id)
            after = runtime.get_active_run_snapshot("assistant", state.session_id)
            return events, before, after, state
        finally:
            runtime.ACTIVE_RUNS.pop(key, None)

    events, before, after, state = anyio.run(run_case)

    assert [event["event"] for event in events] == ["delta", "done"]
    assert before == after
    assert before["running"] is False
    assert before["status"] == "completed"
    assert before["content"] == "final answer"
    assert before["event_count"] == 2
    assert state.done_payload == {"content": "final answer", "new_achievements": []}


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


def test_stop_active_run_is_idempotent_after_user_stop_request(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="assistant",
            session_id="stop-idempotent-session",
            run_id="run-stop-idempotent",
        )
        key = runtime._active_key("siq_assistant", state.session_id)
        runtime.ACTIVE_RUNS[key] = state
        stop_calls = []

        async def fake_stop_run(run_id, *, profile):
            stop_calls.append((run_id, profile))
            return {"id": run_id, "status": "stopped", "profile": profile}

        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        try:
            first = await runtime.stop_active_run("assistant", state.session_id)
            second = await runtime.stop_active_run("siq_assistant", state.session_id)
            return first, second, state, stop_calls
        finally:
            runtime.ACTIVE_RUNS.pop(key, None)

    first, second, state, stop_calls = anyio.run(run_case)

    assert first == {
        "stopped": True,
        "run_id": "run-stop-idempotent",
        "hermes": {"id": "run-stop-idempotent", "status": "stopped", "profile": "siq_assistant"},
    }
    assert second == {
        "stopped": True,
        "run_id": "run-stop-idempotent",
        "detail": "Stop already requested",
    }
    assert stop_calls == [("run-stop-idempotent", "siq_assistant")]
    assert state.profile == "siq_assistant"
    assert [event["event"] for event in state.events] == ["progress", "replace"]


def test_streaming_stop_active_run_keeps_public_call_shape(monkeypatch):
    async def run_case():
        state = agent_runtime_streaming.ActiveRunState(
            profile="assistant",
            session_id="streaming-stop-public-shape-session",
            run_id="run-streaming-stop-public-shape",
        )
        key = agent_runtime_streaming._active_key("siq_assistant", state.session_id)
        agent_runtime_streaming.ACTIVE_RUNS[key] = state
        stop_calls = []

        async def fake_stop_run(run_id, *, profile):
            stop_calls.append((run_id, profile))
            return {"id": run_id, "status": "stopped", "profile": profile}

        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        try:
            result = await agent_runtime_streaming.stop_active_run("assistant", state.session_id)
            return result, state, stop_calls
        finally:
            agent_runtime_streaming.ACTIVE_RUNS.pop(key, None)

    result, state, stop_calls = anyio.run(run_case)

    assert result == {
        "stopped": True,
        "run_id": "run-streaming-stop-public-shape",
        "hermes": {"id": "run-streaming-stop-public-shape", "status": "stopped", "profile": "siq_assistant"},
    }
    assert stop_calls == [("run-streaming-stop-public-shape", "siq_assistant")]
    assert state.user_stop_requested is True
    assert [event["event"] for event in state.events] == ["progress", "replace"]


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


def test_stop_active_run_404_orphan_drains_existing_active_stream(monkeypatch):
    async def run_case():
        state = runtime.ActiveRunState(
            profile="siq_assistant",
            session_id="orphan-stream-drain-session",
            run_id="run-orphan-stream-drain",
        )
        key = runtime._active_key(state.profile, state.session_id)
        runtime.ACTIVE_RUNS[key] = state

        async def fake_stop_run(_run_id, *, profile):
            request = httpx.Request("POST", f"https://hermes.example/runs/{_run_id}/stop")
            response = httpx.Response(404, request=request)
            raise httpx.HTTPStatusError("not found", request=request, response=response)

        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        stream = runtime.stream_active_run_events(
            _Request(),
            profile="assistant",
            session_id=state.session_id,
        )
        events = []

        async def collect_stream():
            async for event in stream:
                events.append(event)

        async with anyio.create_task_group() as tg:
            tg.start_soon(collect_stream)
            await anyio.sleep(0)
            result = await runtime.stop_active_run("assistant", state.session_id)

        return result, events, key in runtime.ACTIVE_RUNS

    result, events, still_active = anyio.run(run_case)

    assert result["hermes"] == {"error": "run_not_found"}
    assert still_active is False
    assert [event["event"] for event in events] == ["progress", "replace", "progress", "replace", "error"]
    assert _event_payload(events[-1]) == {
        "message": runtime.ORPHANED_RUN_MESSAGE,
        "reason": "hermes_run_not_found",
    }


def test_stop_active_run_without_state_returns_not_stopped():
    result = anyio.run(runtime.stop_active_run, "siq_assistant", "missing-active-run")

    assert result == {"stopped": False, "detail": "No active Hermes run"}
