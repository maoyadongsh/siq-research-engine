import json

import anyio

from services import agent_runtime_streaming


class _Request:
    async def is_disconnected(self) -> bool:
        return False


def _event_payload(event: dict[str, str]) -> dict:
    return json.loads(event["data"])


def test_stream_idle_timeout_uses_assistant_timeout_for_assistant_aliases():
    assert (
        agent_runtime_streaming.stream_idle_timeout(
            "siq_assistant",
            assistant_timeout_seconds=11,
            specialist_timeout_seconds=22,
        )
        == 11
    )
    assert (
        agent_runtime_streaming.stream_idle_timeout(
            "assistant",
            assistant_timeout_seconds=11,
            specialist_timeout_seconds=22,
        )
        == 11
    )
    assert (
        agent_runtime_streaming.stream_idle_timeout(
            "siq_analysis",
            assistant_timeout_seconds=11,
            specialist_timeout_seconds=22,
        )
        == 22
    )


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


def test_streaming_progress_append_helpers_keep_event_order_and_payloads():
    async def run_case():
        completed = agent_runtime_streaming.ActiveRunState(
            profile="assistant",
            session_id="streaming-completed-progress-session",
            run_id="run-streaming-completed-progress",
        )
        stopped = agent_runtime_streaming.ActiveRunState(
            profile="assistant",
            session_id="streaming-stopped-progress-session",
            run_id="run-streaming-stopped-progress",
        )
        reasoning = agent_runtime_streaming.ActiveRunState(
            profile="assistant",
            session_id="streaming-reasoning-progress-session",
            run_id="run-streaming-reasoning-progress",
        )

        await agent_runtime_streaming._append_completed_active_run(
            completed,
            {"content": "done"},
            detail="保存完成",
        )
        await agent_runtime_streaming._append_user_stopped_active_run(stopped, "用户停止")
        await agent_runtime_streaming._append_reasoning_active_run(reasoning, "推理" * 120)
        return completed, stopped, reasoning

    completed, stopped, reasoning = anyio.run(run_case)

    assert [event["event"] for event in completed.events] == ["progress", "done"]
    completed_progress = _event_payload(completed.events[0])
    assert completed_progress["status"] == "completed"
    assert completed_progress["title"] == "任务完成"
    assert completed_progress["detail"] == "保存完成"
    assert completed_progress["percent"] == 100

    assert [event["event"] for event in stopped.events] == ["progress", "error"]
    stopped_progress = _event_payload(stopped.events[0])
    assert stopped_progress["status"] == "stopped"
    assert stopped_progress["title"] == "任务已停止"
    assert stopped_progress["detail"] == "用户停止"
    assert stopped.status == "failed"

    assert [event["event"] for event in reasoning.events] == ["reasoning", "progress"]
    reasoning_progress = _event_payload(reasoning.events[1])
    assert reasoning_progress["source"] == "reasoning"
    assert reasoning_progress["title"] == "正在推理"
    assert len(reasoning_progress["detail"]) == 180


def test_project_tool_started_updates_repeated_call_state_and_payload():
    state = agent_runtime_streaming.ActiveRunState(
        profile="assistant",
        session_id="streaming-tool-started-session",
        run_id="run-streaming-tool-started",
    )

    first = agent_runtime_streaming.project_tool_started(
        state,
        tool="terminal",
        preview="python loop.py",
        display_tool_label=lambda tool, preview: f"display:{tool}:{preview}",
        hash_text=lambda text: text,
        repeated_tool_call_limit=2,
    )
    second = agent_runtime_streaming.project_tool_started(
        state,
        tool="terminal",
        preview="python loop.py",
        display_tool_label=lambda tool, preview: f"display:{tool}:{preview}",
        hash_text=lambda text: text,
        repeated_tool_call_limit=2,
    )

    assert first.repeated_call_limit_reached is False
    assert second.repeated_call_limit_reached is True
    assert state.tool_events_since_delta == 2
    assert state.consecutive_same_tool_calls == 2
    assert state.last_tool_label == "terminal"
    assert state.last_tool_preview == "python loop.py"
    assert second.progress_payload["title"] == "正在执行 display:terminal:python loop.py"
    assert second.progress_payload["source"] == "tool"
    assert second.state_event_payload == {
        "status": "started",
        "tool": "terminal",
        "preview": "python loop.py",
    }


def test_project_tool_completed_tracks_consecutive_errors_and_reset_on_success():
    state = agent_runtime_streaming.ActiveRunState(
        profile="assistant",
        session_id="streaming-tool-completed-session",
        run_id="run-streaming-tool-completed",
    )
    state.last_tool_started_signature = "terminal\nmissing-file"
    state.last_tool_preview = "missing-file"

    first = agent_runtime_streaming.project_tool_completed(
        state,
        tool="terminal",
        duration=0.25,
        error=True,
        display_tool_label=lambda tool, preview: f"display:{tool}:{preview}",
        hash_text=lambda text: text,
        consecutive_tool_error_limit=2,
    )
    second = agent_runtime_streaming.project_tool_completed(
        state,
        tool="terminal",
        duration=0.5,
        error=True,
        display_tool_label=lambda tool, preview: f"display:{tool}:{preview}",
        hash_text=lambda text: text,
        consecutive_tool_error_limit=2,
    )
    recovered = agent_runtime_streaming.project_tool_completed(
        state,
        tool="terminal",
        duration=1.0,
        error=False,
        display_tool_label=lambda tool, preview: f"display:{tool}:{preview}",
        hash_text=lambda text: text,
        consecutive_tool_error_limit=2,
    )

    assert first.consecutive_error_limit_reached is False
    assert second.consecutive_error_limit_reached is True
    assert second.progress_payload["status"] == "error"
    assert second.progress_payload["title"] == "display:terminal:missing-file 执行异常"
    assert second.progress_payload["detail"] == "耗时 0.5s"
    assert second.state_event_payload == {
        "status": "completed",
        "tool": "terminal",
        "duration": 0.5,
        "error": True,
    }
    assert state.total_tool_errors == 2
    assert state.last_tool_error_tool == "terminal"
    assert recovered.consecutive_error_limit_reached is False
    assert recovered.progress_payload["status"] == "running"
    assert state.consecutive_tool_errors == 0
    assert state.last_tool_error_signature is None


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
