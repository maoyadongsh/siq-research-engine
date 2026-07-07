import json

import anyio

from services import agent_runtime_streaming


class _Request:
    async def is_disconnected(self) -> bool:
        return False


def _event_payload(event: dict[str, str]) -> dict:
    return json.loads(event["data"])


def test_state_event_content_and_terminal_status_transitions():
    async def run_case():
        state = agent_runtime_streaming.ActiveRunState(
            profile="assistant",
            session_id="streaming-state-transition-session",
            run_id="run-streaming-state-transition",
        )
        await agent_runtime_streaming._append_state_event(state, "run", {"run_id": state.run_id})
        await agent_runtime_streaming._append_state_event(state, "delta", {"content": "hello"})
        await agent_runtime_streaming._append_state_event(state, "replace", {"content": "final"})
        await agent_runtime_streaming._append_state_event(state, "done", {"content": "final"})
        return state

    state = anyio.run(run_case)

    assert [event["event"] for event in state.events] == ["run", "delta", "replace", "done"]
    assert state.content == "final"
    assert state.status == "completed"
    assert state.done_payload == {"content": "final"}


def test_state_event_error_uses_message_before_content():
    async def run_case():
        state = agent_runtime_streaming.ActiveRunState(
            profile="assistant",
            session_id="streaming-state-error-session",
            run_id="run-streaming-state-error",
        )
        await agent_runtime_streaming._append_state_event(
            state,
            "error",
            {"content": "fallback error", "message": "primary error"},
        )
        return state

    state = anyio.run(run_case)

    assert state.status == "failed"
    assert state.error == "primary error"
    assert _event_payload(state.events[0]) == {"content": "fallback error", "message": "primary error"}


def test_stream_active_run_events_respects_offset_until_terminal_event():
    async def run_case():
        state = agent_runtime_streaming.ActiveRunState(
            profile="assistant",
            session_id="streaming-offset-session",
            run_id="run-streaming-offset",
        )
        key = agent_runtime_streaming._active_key("siq_assistant", state.session_id)
        agent_runtime_streaming.ACTIVE_RUNS[key] = state
        try:
            await agent_runtime_streaming._append_state_event(state, "run", {"run_id": state.run_id})
            await agent_runtime_streaming._append_state_event(state, "delta", {"content": "hello"})
            await agent_runtime_streaming._append_completed_active_run(state, {"content": "hello"})

            events = []
            async for event in agent_runtime_streaming.stream_active_run_events(
                _Request(),
                profile="assistant",
                session_id=state.session_id,
                offset=1,
                heartbeat_seconds=0.01,
            ):
                events.append(event)
            return events
        finally:
            agent_runtime_streaming.ACTIVE_RUNS.pop(key, None)

    events = anyio.run(run_case)

    assert [event["event"] for event in events] == ["delta", "progress", "done"]
    assert _event_payload(events[0]) == {"content": "hello"}
    assert _event_payload(events[-1]) == {"content": "hello"}


def test_stream_active_run_events_emits_heartbeat_while_running():
    async def run_case():
        state = agent_runtime_streaming.ActiveRunState(
            profile="assistant",
            session_id="streaming-heartbeat-session",
            run_id="run-streaming-heartbeat",
        )
        key = agent_runtime_streaming._active_key("assistant", state.session_id)
        agent_runtime_streaming.ACTIVE_RUNS[key] = state
        stream = agent_runtime_streaming.stream_active_run_events(
            _Request(),
            profile="siq_assistant",
            session_id=state.session_id,
            heartbeat_seconds=0.001,
        )
        try:
            event = await anext(stream)
            return event
        finally:
            await stream.aclose()
            agent_runtime_streaming.ACTIVE_RUNS.pop(key, None)

    event = anyio.run(run_case)

    assert event["event"] == "progress"
    payload = _event_payload(event)
    assert payload["status"] == "running"
    assert payload["source"] == "runtime"
    assert payload["title"] == "等待模型或工具返回"
