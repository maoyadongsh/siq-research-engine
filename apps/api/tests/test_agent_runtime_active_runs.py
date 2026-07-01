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

        async def fake_stop_run(run_id, *, profile):
            return {"id": run_id, "status": "stopped", "profile": profile}

        monkeypatch.setattr(runtime, "stop_run", fake_stop_run)
        try:
            result = await runtime.stop_active_run("assistant", state.session_id)
            events = await _collect_active_events_limit("siq_assistant", state.session_id, 2)
            return result, events, state
        finally:
            runtime.ACTIVE_RUNS.pop(key, None)

    result, events, state = anyio.run(run_case)

    assert result["stopped"] is True
    assert result["run_id"] == "run-alias-stop-stream"
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
