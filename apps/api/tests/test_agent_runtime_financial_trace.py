import json
import os
import sqlite3
from pathlib import Path

import anyio
from services.agent_runtime_financial_trace import extract_runtime_financial_receipts

from services import agent_chat_runtime as runtime


def _write_session(profile_dir: Path, session_id: str, *, command: str, output: dict, tool_call_id: str = "call-1") -> None:
    session_path = profile_dir / "sessions" / f"session_{session_id}.json"
    session_path.parent.mkdir(parents=True)
    session_path.write_text(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": "calculate"},
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": tool_call_id,
                                "function": {
                                    "name": "terminal",
                                    "arguments": json.dumps({"command": command}),
                                },
                            }
                        ],
                    },
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(
                            {"output": json.dumps(output), "exit_code": 0, "error": None}
                        ),
                    },
                ]
            }
        ),
        encoding="utf-8",
    )


def _create_state_db(profile_dir: Path) -> sqlite3.Connection:
    profile_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(profile_dir / "state.db")
    connection.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            started_at REAL NOT NULL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT,
            tool_call_id TEXT,
            tool_calls TEXT,
            timestamp REAL NOT NULL
        );
        """
    )
    return connection


def _insert_sqlite_turn(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    command: str,
    output: dict,
    timestamp: float,
    tool_call_id: str,
    exit_code: int = 0,
) -> None:
    connection.execute(
        "INSERT OR IGNORE INTO sessions (id, source, started_at) VALUES (?, 'api_server', ?)",
        (session_id, timestamp),
    )
    connection.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?, 'user', 'calculate', ?)",
        (session_id, timestamp),
    )
    connection.execute(
        """
        INSERT INTO messages (session_id, role, content, tool_calls, timestamp)
        VALUES (?, 'assistant', '', ?, ?)
        """,
        (
            session_id,
            json.dumps(
                [
                    {
                        "id": tool_call_id,
                        "function": {
                            "name": "terminal",
                            "arguments": json.dumps({"command": command}),
                        },
                    }
                ]
            ),
            timestamp + 0.1,
        ),
    )
    connection.execute(
        """
        INSERT INTO messages (session_id, role, content, tool_call_id, timestamp)
        VALUES (?, 'tool', ?, ?, ?)
        """,
        (
            session_id,
            json.dumps({"output": json.dumps(output), "exit_code": exit_code, "error": None}),
            tool_call_id,
            timestamp + 0.2,
        ),
    )


def _ratio_payload() -> dict:
    return {
        "status": "ok",
        "operation": "ratio",
        "input": {
            "numerator": "40",
            "numerator_unit": "HKD million",
            "denominator": "100",
            "denominator_unit": "HKD million",
        },
        "result": {"ratio": "0.4", "percent": "40"},
    }


def _normalize_payload() -> dict:
    return {
        "status": "ok",
        "operation": "normalize_amount",
        "input": {"value": "4427571", "unit": "千元", "currency": "CNY"},
        "result": {
            "native_base_value": "4427571000",
            "native_100m_value": "44.27571",
            "cny_base_value": "4427571000",
            "cny_100m_value": "44.27571",
        },
    }


def test_extracts_single_allowlisted_current_turn_receipt(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
        output=_ratio_payload(),
    )

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert len(receipts) == 1
    assert receipts[0]["operation"] == "ratio"
    assert receipts[0]["receipt_tool_call_id"] == "call-1"
    assert receipts[0]["receipt_source"] == "hermes_session_tool"


def test_extracts_allowlisted_amount_normalization_receipt(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:normalize-session"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json normalize --value 4427571 --unit 千元 --currency CNY",
        output=_normalize_payload(),
    )

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert len(receipts) == 1
    assert receipts[0]["operation"] == "normalize_amount"
    assert receipts[0]["result"]["native_100m_value"] == "44.27571"


def test_extracts_only_latest_exact_sqlite_session_turn(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:sqlite-session"
    other_session_id = "siq:siq_assistant:other-session"
    connection = _create_state_db(profile_dir)
    try:
        old_payload = _ratio_payload()
        old_payload["result"] = {"ratio": "0.2", "percent": "20"}
        _insert_sqlite_turn(
            connection,
            session_id,
            command=f"python3 {script} --format json ratio --numerator 20 --denominator 100",
            output=old_payload,
            timestamp=100.0,
            tool_call_id="old-call",
        )
        current_payload = _ratio_payload()
        _insert_sqlite_turn(
            connection,
            session_id,
            command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
            output=current_payload,
            timestamp=200.0,
            tool_call_id="current-call",
        )
        other_payload = _ratio_payload()
        other_payload["result"] = {"ratio": "0.9", "percent": "90"}
        _insert_sqlite_turn(
            connection,
            other_session_id,
            command=f"python3 {script} --format json ratio --numerator 90 --denominator 100",
            output=other_payload,
            timestamp=300.0,
            tool_call_id="other-call",
        )
        connection.commit()
    finally:
        connection.close()

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert len(receipts) == 1
    assert receipts[0]["receipt_tool_call_id"] == "current-call"
    assert receipts[0]["result"]["percent"] == "40"


def test_prefers_newer_sqlite_turn_over_stale_json_session(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:migrated-session"
    stale_payload = _ratio_payload()
    stale_payload["result"] = {"ratio": "0.2", "percent": "20"}
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 20 --denominator 100",
        output=stale_payload,
        tool_call_id="json-call",
    )
    json_path = profile_dir / "sessions" / f"session_{session_id}.json"
    os.utime(json_path, ns=(100_000_000_000, 100_000_000_000))
    connection = _create_state_db(profile_dir)
    try:
        _insert_sqlite_turn(
            connection,
            session_id,
            command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
            output=_ratio_payload(),
            timestamp=200.0,
            tool_call_id="sqlite-call",
        )
        connection.commit()
    finally:
        connection.close()

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert len(receipts) == 1
    assert receipts[0]["receipt_tool_call_id"] == "sqlite-call"
    assert receipts[0]["result"]["percent"] == "40"


def test_future_mtime_legacy_json_cannot_override_canonical_sqlite_turn(tmp_path):
    logical_profile = tmp_path / "siq_assistant"
    live_profile = tmp_path / "finsight_assistant"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:migrated-session"
    stale_payload = _ratio_payload()
    stale_payload["result"] = {"ratio": "0.2", "percent": "20"}
    _write_session(
        logical_profile,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 20 --denominator 100",
        output=stale_payload,
        tool_call_id="json-stale-call",
    )
    json_path = logical_profile / "sessions" / f"session_{session_id}.json"
    os.utime(json_path, ns=(999_000_000_000, 999_000_000_000))
    connection = _create_state_db(live_profile)
    try:
        _insert_sqlite_turn(
            connection,
            session_id,
            command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
            output=_ratio_payload(),
            timestamp=200.0,
            tool_call_id="sqlite-current-call",
        )
        connection.commit()
    finally:
        connection.close()

    receipts = extract_runtime_financial_receipts(
        profile_dir=logical_profile,
        profile_dirs=(live_profile,),
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert len(receipts) == 1
    assert receipts[0]["receipt_tool_call_id"] == "sqlite-current-call"
    assert receipts[0]["result"]["percent"] == "40"


def test_rejects_sqlite_receipt_with_nonzero_exit_status(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:failed-sqlite-session"
    connection = _create_state_db(profile_dir)
    try:
        _insert_sqlite_turn(
            connection,
            session_id,
            command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
            output=_ratio_payload(),
            timestamp=200.0,
            tool_call_id="failed-call",
            exit_code=1,
        )
        connection.commit()
    finally:
        connection.close()

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert receipts == ()


def test_extracts_only_newest_exact_session_across_profile_roots(tmp_path):
    logical_profile = tmp_path / "siq_assistant"
    live_profile = tmp_path / "finsight_assistant"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    old_payload = _ratio_payload()
    new_payload = _ratio_payload()
    new_payload["result"] = {"ratio": "0.3", "percent": "30"}
    _write_session(
        logical_profile,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
        output=old_payload,
        tool_call_id="old-call",
    )
    _write_session(
        live_profile,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 30 --denominator 100",
        output=new_payload,
        tool_call_id="new-call",
    )
    old_path = logical_profile / "sessions" / f"session_{session_id}.json"
    new_path = live_profile / "sessions" / f"session_{session_id}.json"
    os.utime(old_path, ns=(1_000_000_000, 1_000_000_000))
    os.utime(new_path, ns=(2_000_000_000, 2_000_000_000))

    receipts = extract_runtime_financial_receipts(
        profile_dir=logical_profile,
        profile_dirs=(live_profile,),
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert len(receipts) == 1
    assert receipts[0]["receipt_tool_call_id"] == "new-call"
    assert receipts[0]["result"]["percent"] == "30"


def test_runtime_includes_live_finsight_profile_as_receipt_candidate(tmp_path, monkeypatch):
    logical_profile = tmp_path / "siq_assistant"
    captured = {}

    def fake_extract(**kwargs):
        captured.update(kwargs)
        return ()

    monkeypatch.setitem(runtime.HERMES_PROFILE_ROOTS, "siq_assistant", logical_profile)
    monkeypatch.setattr(runtime, "HERMES_PROFILES_ROOT", tmp_path)
    monkeypatch.setattr(runtime.agent_runtime_financial_trace, "extract_runtime_financial_receipts", fake_extract)

    assert runtime._trusted_financial_receipts("siq_assistant", "chat-session") == ()
    assert captured["profile_dir"] == logical_profile
    assert captured["profile_dirs"] == (tmp_path / "finsight_assistant",)


def test_openshell_runtime_reads_receipts_from_routed_fresh_snapshot(tmp_path, monkeypatch):
    run_id = "canary-0123456789ab"
    snapshot = tmp_path / "var/openshell/siq-analysis/runtime-snapshots" / run_id
    (snapshot / "runtime-state").mkdir(parents=True)
    captured = {}

    def fake_extract(**kwargs):
        captured.update(kwargs)
        return ()

    route = runtime.HermesRunRoute(
        target="openshell",
        base="http://127.0.0.1:28651/v1/runs",
        model="siq_analysis",
        authorization="Bearer redacted",
        session_namespace=f"siq:openshell:{run_id}:siq_analysis:cn:scope",
        canary_run_id=run_id,
    )
    monkeypatch.setattr(runtime, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(runtime.agent_runtime_financial_trace, "extract_runtime_financial_receipts", fake_extract)

    assert runtime._trusted_financial_receipts(
        "siq_analysis",
        "user-1-analysis-session",
        route=route,
    ) == ()
    assert captured["profile_dir"] == snapshot
    assert captured["profile_dirs"] == (snapshot / "runtime-state",)
    assert captured["hermes_session_id"] == (
        f"siq:openshell:{run_id}:siq_analysis:cn:scope:user-1-analysis-session"
    )


def test_rejects_piped_or_chained_financial_command(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100 | head",
        output=_ratio_payload(),
    )

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert receipts == ()


def test_rejects_unpaired_or_mixed_stdout_receipt(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
        output=_ratio_payload(),
        tool_call_id="call-command",
    )
    path = profile_dir / "sessions" / f"session_{session_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["messages"][-1]["tool_call_id"] = "call-other"
    path.write_text(json.dumps(payload), encoding="utf-8")

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert receipts == ()


def test_rejects_operation_that_does_not_belong_to_invoked_script(tmp_path):
    profile_dir = tmp_path / "profile"
    script = tmp_path / "financial_calculator.py"
    script.write_text("", encoding="utf-8")
    session_id = "siq:siq_assistant:test-session"
    payload = _ratio_payload()
    payload["operation"] = "goodwill_reconciliation"
    _write_session(
        profile_dir,
        session_id,
        command=f"python3 {script} --format json ratio --numerator 40 --denominator 100",
        output=payload,
    )

    receipts = extract_runtime_financial_receipts(
        profile_dir=profile_dir,
        hermes_session_id=session_id,
        allowed_script_paths={"financial_calculator.py": script},
    )

    assert receipts == ()


def test_runtime_retries_financial_receipt_after_terminal_event(monkeypatch):
    calls = 0
    delays: list[float] = []
    receipt = {"operation": "ratio", "receipt_source": "hermes_session_tool"}

    def fake_receipts(_profile, _session_id, *, route=None):
        assert route is None
        nonlocal calls
        calls += 1
        return () if calls == 1 else (receipt,)

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(runtime, "_trusted_financial_receipts", fake_receipts)
    monkeypatch.setattr(runtime.asyncio, "sleep", fake_sleep)

    async def run_case():
        return await runtime._trusted_financial_receipts_after_run(
            "siq_assistant",
            "session-id",
            message="前两大商誉占比是多少？",
            reply="前两大商誉占比为 86.93%。",
        )

    result = anyio.run(run_case)

    assert result == (receipt,)
    assert calls == 2
    assert delays == [0.02]


def test_runtime_does_not_wait_for_non_financial_receipt(monkeypatch):
    calls = 0

    def fake_receipts(_profile, _session_id, *, route=None):
        assert route is None
        nonlocal calls
        calls += 1
        return ()

    async def unexpected_sleep(_delay):
        raise AssertionError("non-financial replies must not wait for a financial receipt")

    monkeypatch.setattr(runtime, "_trusted_financial_receipts", fake_receipts)
    monkeypatch.setattr(runtime.asyncio, "sleep", unexpected_sleep)

    async def run_case():
        return await runtime._trusted_financial_receipts_after_run(
            "siq_assistant",
            "session-id",
            message="请概述公司战略。",
            reply="公司战略聚焦产品升级。",
        )

    result = anyio.run(run_case)

    assert result == ()
    assert calls == 1
