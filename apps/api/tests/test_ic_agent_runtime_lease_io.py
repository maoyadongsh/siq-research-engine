import asyncio
import threading

from services import ic_agent_runtime


def test_ic_terminal_lease_write_runs_off_event_loop_thread(tmp_path, monkeypatch):
    event_loop_thread = threading.get_ident()
    seen = {}

    def fake_finish(store_path, **kwargs):
        seen.update(
            {
                "thread": threading.get_ident(),
                "store_path": store_path,
                **kwargs,
            }
        )
        return {"status": kwargs["status"]}

    monkeypatch.setattr(ic_agent_runtime, "finish_ic_task", fake_finish)
    store_path = tmp_path / "ic_task_leases.json"

    result = asyncio.run(
        ic_agent_runtime._finish_ic_task_off_thread(
            store_path,
            task_key="DEAL-001:R1:siq_ic_strategist",
            owner="worker-a",
            now="2026-07-13T09:00:00Z",
            status="succeeded",
        )
    )

    assert result == {"status": "succeeded"}
    assert seen["thread"] != event_loop_thread
    assert seen["store_path"] == store_path
    assert seen["task_key"] == "DEAL-001:R1:siq_ic_strategist"
